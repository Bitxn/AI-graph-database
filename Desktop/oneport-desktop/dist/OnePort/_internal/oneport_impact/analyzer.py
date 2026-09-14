"""
The orchestrator: run the deterministic engines, assemble a BlastRadius, and — in
gate mode — distil findings whose JSON matches the Oneport contract so `op ship`
can merge impact with the other gates.

Order per target: call graph (who calls it) + co-change (what moves with it) +
ownership (who to ask) → optional LLM risk verdict. Facts first, judgment last.
"""

from __future__ import annotations

import time
from pathlib import Path

from oneport_impact import advisor
from oneport_impact.callgraph import CallGraph, build_call_graph
from oneport_impact.cochange import CoChangeIndex, build_cochange_index
from oneport_impact.config import Config
from oneport_impact.exceptions import ResolveError
from oneport_impact.guidelines import load_guidelines
from oneport_impact.integrations import local_git
from oneport_impact.ownership import owners_of
from oneport_impact.result import (
    BlastRadius, CallSite, Finding, ImpactReport, Severity, Symbol, TestRef,
    compute_blocking,
)

_TEST_HINTS = ("test_", "_test", "/tests/", "tests/", "conftest", "/testing/")


def is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    return ("/tests/" in p or p.startswith("tests/") or "/testing/" in p
            or base.startswith("test_") or base.endswith("_test.py") or "conftest" in base)


class Analyzer:
    def __init__(self, config: Config, root: str | Path = ".") -> None:
        self.config = config
        self.root = Path(root).resolve()
        self._graph: CallGraph | None = None
        self._cochange: CoChangeIndex | None = None
        self._guidelines = load_guidelines(config.guidelines_path)
        self._has_git = local_git.is_repo(self.root)

    # ── shared, lazily-built indexes ─────────────────────────────────────────
    @property
    def graph(self) -> CallGraph:
        if self._graph is None:
            self._graph = build_call_graph(self.root)
        return self._graph

    @property
    def cochange(self) -> CoChangeIndex:
        if self._cochange is None:
            self._cochange = (
                build_cochange_index(self.root, max_commits=self.config.thresholds.max_commits)
                if self._has_git else CoChangeIndex()
            )
        return self._cochange

    # ── analyze mode ─────────────────────────────────────────────────────────
    def analyze(self, query: str, use_llm: bool = True) -> ImpactReport:
        start = time.monotonic()
        report = ImpactReport(mode="analyze", target=query, model=self.config.model)

        symbols = self.graph.resolve(query)
        as_file = self._as_file(query)
        if as_file and not (symbols and not as_file):
            file_syms = self.graph.symbols_in_file(as_file)
            br = self._blast_for_symbols(as_file, "file", file_syms, file_scope=as_file)
        elif symbols:
            br = self._blast_for_symbols(query, "symbol", symbols)
        else:
            target_path = self.root / query
            rel = query.replace("\\", "/")
            if target_path.is_file() and target_path.suffix.lower() not in (".py", ".pyi"):
                # A non-Python file: call-graph fan-in is unavailable, but co-change
                # and ownership are git-based and language-agnostic, so we can still
                # give a real, honest blast radius rather than an outright refusal.
                if not self._has_git:
                    raise ResolveError(
                        f"'{query}' isn't Python and this isn't a git repo — call-graph "
                        "fan-in needs Python; co-change and ownership need git.")
                br = self._blast_for_symbols(rel, "file", [], file_scope=rel, fan_in_available=False)
                report.notes.append(
                    f"'{query}' is not a Python file — call-graph fan-in is unavailable. "
                    "The blast radius below is from git co-change and ownership.")
            elif self.graph.files_parsed == 0:
                raise ResolveError(
                    f"No Python files found in {self.root.name}. Impact analyzes Python call "
                    "graphs (.py); for other languages, pass a repo-relative FILE path to get "
                    "its git co-change and ownership blast radius.")
            else:
                raise ResolveError(
                    f"Could not find '{query}' as a symbol or a file in {self.root.name}. "
                    "Try a function/class name, a dotted qualname, or a repo-relative path.")

        skipped = self._ambiguous_skipped(br)
        if skipped:
            report.notes.append(
                f"{skipped} symbol(s) here have names too common to attribute callers "
                "reliably (e.g. __init__, get); their call sites are excluded to keep "
                "fan-in honest.")
        if not self._has_git:
            report.notes.append("Not a git repo — co-change and ownership were skipped.")
        frozen = self.graph.frozen_files_skipped
        if frozen:
            report.notes.append(
                f"{frozen} migration file(s) excluded — they record schema changes that "
                "already ran, so they can't be broken by a change made today.")
        if use_llm and self.config.has_key:
            report.total_tokens += advisor.assess(br, self.config, self._guidelines)

        report.blast_radii = [br]
        report.elapsed_ms = int((time.monotonic() - start) * 1000)
        return report

    # ── check (gate) mode ────────────────────────────────────────────────────
    def check(self, mode: str, use_llm: bool = True, fail_on: Severity = Severity.ERROR) -> ImpactReport:
        start = time.monotonic()
        report = ImpactReport(mode="check", target=mode, model=self.config.model)
        local_git.require_repo(self.root)

        changed = [f for f in local_git.changed_files(self.root, mode)]
        changed_set = set(changed)
        report.changed_files = len(changed)
        if not changed:
            report.notes.append(f"No {mode} changes to analyze.")
            report.elapsed_ms = int((time.monotonic() - start) * 1000)
            return report

        # Call-graph fan-in is Python-only. But co-change and ownership come from
        # git history and work for ANY file — so a non-Python change still gets a
        # real, honest blast radius (what moves with it, who owns it), with fan-in
        # explicitly marked unavailable rather than reported as a fake "0 callers".
        report.analyzed_files = sum(1 for f in changed if f.endswith((".py", ".pyi")))
        py_change = report.analyzed_files > 0
        if not py_change and not self._has_git:
            report.notes.append(
                "No Python files in this change and not a git repo — nothing to analyze. "
                "Call-graph fan-in needs Python; co-change and ownership need git.")
            report.elapsed_ms = int((time.monotonic() - start) * 1000)
            return report
        if not py_change:
            report.notes.append(
                "No Python files in this change — call-graph fan-in is Python-only, so it "
                "is marked unavailable. Blast radius below is from git co-change and "
                "ownership, which are language-agnostic.")

        th = self.config.thresholds
        radii: list[BlastRadius] = []
        for f in changed:
            if not (self.root / f).exists():
                continue  # deleted file
            is_py = f.endswith((".py", ".pyi"))
            file_syms = self.graph.symbols_in_file(f) if is_py else []
            br = self._blast_for_symbols(f, "file", file_syms, file_scope=f, fan_in_available=is_py)
            radii.append(br)

            # 1) wide blast radius (only meaningful when fan-in was measured)
            if br.fan_in_available and br.fan_in >= th.fan_in_error:
                report.findings.append(Finding(
                    severity=Severity.ERROR, file=f, line=1, rule_id="IMP001",
                    message=(f"Changing {f} affects {br.fan_in} call sites across "
                             f"{br.caller_files} files — very wide blast radius."),
                    fix="Split the change, add a compatibility shim, or land it behind a flag; "
                        "review the top callers before merging."))
            elif br.fan_in >= th.fan_in_warn:
                report.findings.append(Finding(
                    severity=Severity.WARNING, file=f, line=1, rule_id="IMP001",
                    message=(f"Changing {f} affects {br.fan_in} call sites across "
                             f"{br.caller_files} files — wide blast radius."),
                    fix="Check the callers listed in the analysis before merging."))

            # 2) companions you didn't touch
            for p in br.partners:
                if (p.confidence >= th.cochange_confidence and p.together >= th.cochange_min_together
                        and p.path not in changed_set):
                    report.findings.append(Finding(
                        severity=Severity.WARNING, file=f, line=1, rule_id="IMP010",
                        message=(f"You changed {f} but not {p.path}, which changes with it "
                                 f"{p.pct}% of the time ({p.together}/{p.target_commits} commits)."),
                        fix=f"Confirm {p.path} doesn't also need updating for this change."))

        # LLM verdict on the single widest-radius file (cheap; enriches the report).
        if use_llm and self.config.has_key and radii:
            widest = max(radii, key=lambda b: b.fan_in)
            if widest.fan_in > 0:
                report.total_tokens += advisor.assess(widest, self.config, self._guidelines)

        # Approved waivers keep findings in the report but exclude them from the gate.
        from oneport_impact.waivers import apply_waivers, load_waivers
        waived = apply_waivers(report.findings, load_waivers(self.root))
        if waived:
            report.notes.append(f"{waived} finding(s) waived via .oneport/impact-waivers.yml "
                                "— shown but not blocking.")

        report.findings.sort(key=lambda f: (-f.severity.rank, f.file))
        report.blast_radii = radii
        report.blocking = compute_blocking(report.findings, fail_on)
        report.elapsed_ms = int((time.monotonic() - start) * 1000)
        return report

    # ── core: assemble a BlastRadius from the engines ────────────────────────
    def _blast_for_symbols(
        self, target: str, kind: str, symbols: list[Symbol], file_scope: str | None = None,
        fan_in_available: bool = True,
    ) -> BlastRadius:
        # De-dupe callers by (file,line,called); a call site inside the target's own
        # file is internal, not external blast radius.
        seen: set[tuple] = set()
        callers: list[CallSite] = []
        for sym in symbols:
            for c in self.graph.callers_of(sym):
                if file_scope and c.file == file_scope:
                    continue
                key = (c.file, c.line, c.called)
                if key in seen:
                    continue
                seen.add(key)
                callers.append(c)

        # co-change + ownership key off the file(s) involved.
        files = sorted({s.file for s in symbols} or ({file_scope} if file_scope else set()))
        partners = []
        target_commits = 0
        owners = []
        if self._has_git and files:
            primary = file_scope or files[0]
            partners = self.cochange.partners(
                primary, min_together=2, top=12)
            target_commits = self.cochange.target_commit_count(primary)
            owners = owners_of(self.root, primary, top=4)

        tests = [
            TestRef(test=c.caller_qualname, file=c.file, line=c.line)
            for c in callers if is_test_path(c.file)
        ]
        non_test_callers = [c for c in callers if not is_test_path(c.file)]

        return BlastRadius(
            target=target, target_kind=kind, symbols=symbols,
            callers=non_test_callers, tests=tests, partners=partners, owners=owners,
            fan_in=len(non_test_callers),
            caller_files=len({c.file for c in non_test_callers}),
            target_commits=target_commits,
            fan_in_available=fan_in_available,
        )

    def _ambiguous_skipped(self, br: BlastRadius) -> int:
        return sum(1 for s in br.symbols if not self.graph.attributable(s))

    def _as_file(self, query: str) -> str | None:
        rel = query.replace("\\", "/")
        if (self.root / rel).is_file() and rel.endswith(".py"):
            return rel
        return None

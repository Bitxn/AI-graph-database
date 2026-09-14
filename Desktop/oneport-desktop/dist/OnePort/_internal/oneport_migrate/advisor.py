"""
The LLM judgment layer.

Advisor.assess() takes the deterministic findings plus the migration sources
and repo context, calls the model once, and returns an Assessment. It NEVER
creates or removes findings — it annotates them (blast_radius), proposes a
safe rewrite plan, writes the verdict, and applies severity adjustments that
are justified by explicit team guidelines only.

Failure policy: the LLM layer must never break the gate. Any model or parse
error degrades to an empty Assessment with a note — the deterministic findings
and exit code stand on their own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from oneport_migrate.config import Config
from oneport_migrate.exceptions import OneportMigrateError
from oneport_migrate.llm import call_llm
from oneport_migrate.operations import ParsedMigration
from oneport_migrate.prompt_builder import build_prompt
from oneport_migrate.result import Finding, Severity, compute_blocking


@dataclass
class Assessment:
    verdict: str = ""
    rewrite_plan: list[str] = field(default_factory=list)
    ok: bool = False
    note: str = ""
    total_tokens: int = 0


def _parse_json(raw: str) -> dict:
    """Parse the model's JSON envelope, tolerating the ways small models wrap it.

    gemini-2.5-flash is inconsistent: sometimes clean JSON, sometimes fenced,
    sometimes with a "Here is the assessment:" preamble or a trailing note.
    We strip fences, then fall back to extracting the outermost {...} span, so
    prose around otherwise-valid JSON doesn't cost us the verdict/plan.
    (A response truncated mid-JSON by the token limit still legitimately fails
    and degrades — that's what the higher max_tokens budget is for.)
    """
    text = raw.strip()
    if text.startswith("```"):
        # Drop the opening fence line (```json / ```) and any closing fence.
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip()

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            obj = json.loads(text[start:end + 1])  # may still raise → caller degrades
        else:
            raise
    # Callers do data.get(...): a model that returns a bare array or scalar would
    # otherwise crash with AttributeError. Force a dict; anything else degrades.
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object envelope")
    return obj


class Advisor:
    def __init__(self, config: Config, guidelines: str = "") -> None:
        self.config = config
        self.guidelines = guidelines

    def assess(
        self,
        findings: list[Finding],
        migrations: list[ParsedMigration],
    ) -> Assessment:
        """Annotate findings in place; return verdict/plan/status.

        Mutates: finding.blast_radius, and finding.severity when a
        guideline-backed adjustment matches. Callers must recompute blocking
        afterwards (see apply()).
        """
        repo_context = gather_repo_context(
            migrations, max_chars=self.config.llm.max_context_chars // 2
        )
        prompt = build_prompt(
            findings=findings,
            migrations=migrations,
            db=self.config.db,
            repo_context=repo_context,
            guidelines=self.guidelines,
            max_context_chars=self.config.llm.max_context_chars,
        )

        try:
            raw, tokens = call_llm(
                model=self.config.model,
                api_key=self.config.api_key,
                system=prompt.system,
                user=prompt.user,
                max_tokens=self.config.max_tokens,
            )
        except OneportMigrateError as exc:
            return Assessment(note=f"LLM layer skipped: {exc}")

        try:
            data = _parse_json(raw)
        except (json.JSONDecodeError, ValueError):
            return Assessment(
                note="LLM layer degraded: model returned non-JSON output.",
                total_tokens=tokens,
            )

        self._attach_blast_radius(findings, data.get("blast_radius") or [])
        self._apply_severity_adjustments(findings, data.get("severity_adjustments") or [])

        return Assessment(
            verdict=str(data.get("verdict") or "").strip(),
            rewrite_plan=[str(s) for s in (data.get("rewrite_plan") or [])],
            ok=True,
            total_tokens=tokens,
        )

    # ── Merging model output back onto findings ────────────────────────────────

    def _attach_blast_radius(self, findings: list[Finding], items: list) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            match = _match_finding(findings, item)
            if match is not None and not match.blast_radius:
                match.blast_radius = str(item.get("assessment") or "").strip()

    def _apply_severity_adjustments(self, findings: list[Finding], items: list) -> None:
        # Hard guard: without guidelines there is nothing that can justify an
        # adjustment, whatever the model returned.
        if not self.guidelines:
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                new_severity = Severity(str(item.get("severity", "")))
            except ValueError:
                continue
            match = _match_finding(findings, item)
            if match is None:
                continue
            reason = str(item.get("reason") or "").strip()
            if not reason:
                continue
            match.severity = new_severity
            note = f"severity adjusted per team guideline: {reason}"
            match.blast_radius = (
                f"{match.blast_radius}\n{note}".strip() if match.blast_radius else note
            )


def _match_finding(findings: list[Finding], item: dict) -> Finding | None:
    """Match a model item to a finding: rule_id + file + line, falling back to
    rule_id + file, then rule_id alone (models are sloppy with line numbers)."""
    rule_id = str(item.get("rule_id", ""))
    file = str(item.get("file", ""))
    line = item.get("line")

    by_rule = [f for f in findings if f.rule_id == rule_id]
    if not by_rule:
        return None
    by_file = [f for f in by_rule if f.file == file or file.endswith(f.file) or f.file.endswith(file)]
    pool = by_file or by_rule
    if isinstance(line, int):
        for f in pool:
            if f.line == line:
                return f
    return pool[0]


def apply(assessment: Assessment, findings: list[Finding]) -> bool:
    """Recompute blocking from the FULL finding set after any adjustments."""
    return compute_blocking(findings)


# ── Repo context gathering ─────────────────────────────────────────────────────

def gather_repo_context(migrations: list[ParsedMigration], max_chars: int = 12_000) -> str:
    """
    Best-effort model/schema context for local targets.

    Django: app/migrations/0002_x.py → app/models.py (the convention).
    Alembic: look for models.py / db.py / schema.py near the migration's
    parents (alembic/versions/xxx.py → project root usually two levels up).

    PR targets have no local files — the checker passes migrations whose paths
    don't exist locally, and this returns "" for them, which the prompt states
    honestly.
    """
    seen: set[Path] = set()
    sections: list[str] = []
    budget = max_chars

    for m in migrations:
        for candidate in _context_candidates(Path(m.file), m.framework):
            if candidate in seen or not candidate.is_file():
                continue
            seen.add(candidate)
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            block = f"=== {candidate} ===\n{content.strip()}"
            if len(block) > budget:
                block = block[:budget] + "\n... (truncated)"
            sections.append(block)
            budget -= len(block)
            if budget <= 0:
                return "\n\n".join(sections)

    return "\n\n".join(sections)


def _context_candidates(migration_path: Path, framework: str) -> list[Path]:
    candidates: list[Path] = []
    parent = migration_path.resolve().parent
    if framework == "django":
        # app/migrations/0002_x.py → app/models.py (or app/models/ package)
        app_dir = parent.parent if parent.name == "migrations" else parent
        candidates.append(app_dir / "models.py")
        models_pkg = app_dir / "models"
        if models_pkg.is_dir():
            candidates.extend(sorted(models_pkg.glob("*.py"))[:5])
    else:
        # alembic/versions/xxx.py → walk up a few levels looking for model files
        for ancestor in [parent, parent.parent, parent.parent.parent]:
            for name in ("models.py", "db.py", "schema.py", "tables.py"):
                candidates.append(ancestor / name)
            models_pkg = ancestor / "models"
            if models_pkg.is_dir():
                candidates.extend(sorted(models_pkg.glob("*.py"))[:5])
    return candidates

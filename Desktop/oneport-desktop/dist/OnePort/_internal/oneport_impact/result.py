"""
Result types for oneport-impact.

Two consumers share these:
  * `analyze` mode returns a rich BlastRadius the inline formatter renders in full;
  * `check` (gate) mode distils the same facts into `Finding`s whose JSON matches
    the Oneport result contract, so `op ship` can merge impact with the other gates.

Everything except `verdict`/`reason` is produced deterministically. The model only
fills the judgment fields — it never invents a caller, a partner, or an owner.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "error": 2, "critical": 3}[self.value]

    def __ge__(self, other: "Severity") -> bool:  # type: ignore[override]
        return self.rank >= other.rank

    def __gt__(self, other: "Severity") -> bool:  # type: ignore[override]
        return self.rank > other.rank


# ── Deterministic facts ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Symbol:
    """A function / method / class definition site."""
    name: str            # simple name, e.g. "charge"
    qualname: str        # module-qualified, e.g. "billing.stripe.Client.charge"
    file: str            # repo-relative path
    line: int
    kind: str            # "function" | "method" | "class"


@dataclass(frozen=True)
class CallSite:
    """A place that calls a symbol by name."""
    caller_qualname: str  # the enclosing function/method, or "<module>"
    file: str
    line: int
    called: str


@dataclass(frozen=True)
class CoChangePartner:
    """A file that has historically changed in the same commits as the target."""
    path: str
    together: int         # commits where both changed
    target_commits: int   # commits that touched the target at all
    confidence: float     # together / target_commits  (0..1)

    @property
    def pct(self) -> int:
        return round(self.confidence * 100)


@dataclass(frozen=True)
class Owner:
    name: str
    commits: int          # commits this author made to the target
    last_date: str        # ISO date of their most recent change
    codeowner: bool = False   # listed in CODEOWNERS for this path


@dataclass(frozen=True)
class TestRef:
    """A test that references (and so plausibly exercises) the target."""
    test: str             # qualified test name if resolvable, else file
    file: str
    line: int


@dataclass
class Finding:
    """Gate-mode finding. JSON shape matches the Oneport result contract."""
    severity: Severity
    message: str
    file: str = ""
    line: int = 0
    rule_id: str = ""
    fix: str = ""
    category: str = "impact"
    # An approved waiver keeps the finding in the report but stops it blocking CI.
    waived: bool = False
    waiver_reason: str = ""

    @property
    def gating_severity(self) -> Severity:
        """Severity for gate math: a waived finding never blocks."""
        return Severity.INFO if self.waived else self.severity

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


# ── The report ─────────────────────────────────────────────────────────────────

@dataclass
class BlastRadius:
    """Everything known about the impact of touching one target (symbol or file)."""
    target: str                              # the thing asked about (symbol/file)
    target_kind: str                         # "symbol" | "file"
    symbols: list[Symbol] = field(default_factory=list)   # defs the target covers
    callers: list[CallSite] = field(default_factory=list)
    tests: list[TestRef] = field(default_factory=list)
    partners: list[CoChangePartner] = field(default_factory=list)
    owners: list[Owner] = field(default_factory=list)

    # Deterministic scalar signals used by both the risk score and the gate.
    fan_in: int = 0                          # distinct call sites reaching the target
    caller_files: int = 0                    # distinct files that call it
    target_commits: int = 0                  # commits touching the target file(s)
    # False when the call graph couldn't measure fan-in for this target — e.g. a
    # non-Python file. Then fan_in=0 means "unknown", NOT "nobody calls it"; the
    # blast radius here comes from co-change + ownership (git, language-agnostic).
    fan_in_available: bool = True

    # LLM judgment (empty when --no-llm or no key).
    verdict: str = ""                        # one-paragraph risk assessment
    riskiest: str = ""                       # the single consumer to watch
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "target_kind": self.target_kind,
            "fan_in": self.fan_in,
            "fan_in_available": self.fan_in_available,
            "caller_files": self.caller_files,
            "target_commits": self.target_commits,
            "verdict": self.verdict,
            "riskiest": self.riskiest,
            "reason": self.reason,
            "symbols": [asdict(s) for s in self.symbols],
            "callers": [asdict(c) for c in self.callers],
            "tests": [asdict(t) for t in self.tests],
            "partners": [asdict(p) for p in self.partners],
            "owners": [asdict(o) for o in self.owners],
        }


@dataclass
class ImpactReport:
    """
    The top-level result of a run.

    `blast_radii` holds one entry per analyzed target (one for `analyze`, one per
    changed file for `check`). `findings` is the gate view distilled from them.
    `blocking` is computed once on the FULL finding set (like the sibling tools),
    so a display filter can never hide a blocking finding from the exit code.
    """
    blast_radii: list[BlastRadius] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    mode: str = "analyze"                    # "analyze" | "check"
    target: str = ""
    model: str = ""
    total_tokens: int = 0
    elapsed_ms: int = 0
    blocking: bool = False
    notes: list[str] = field(default_factory=list)
    # Scope of a `check` run: how many files changed vs. how many Impact could
    # actually analyze (Python only). When files changed but analyzed_files == 0,
    # the run verified NOTHING — the report must say "N/A", not imply "CLEAR".
    changed_files: int = 0
    analyzed_files: int = 0
    # Carried for --post on PR targets.
    diff: str = field(default="", repr=False)
    pr_ref: dict[str, Any] | None = None

    @property
    def has_blocking(self) -> bool:
        return self.blocking

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "target": self.target,
            "model": self.model,
            "total_tokens": self.total_tokens,
            "elapsed_ms": self.elapsed_ms,
            "blocking": self.blocking,
            "counts": self.counts(),
            "changed_files": self.changed_files,
            # `files_analyzed` is the orchestrator's scope key: 0 here means the
            # gate examined nothing, so `op ship` shows a skip, not a green pass.
            "files_analyzed": self.analyzed_files,
            "notes": self.notes,
            "findings": [f.to_dict() for f in self.findings],
            "blast_radii": [b.to_dict() for b in self.blast_radii],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def compute_blocking(findings: list[Finding], fail_on: Severity = Severity.ERROR) -> bool:
    # Waived findings are excluded from the gate entirely — never block, at any
    # --fail-on level.
    return any(f.severity >= fail_on for f in findings if not f.waived)

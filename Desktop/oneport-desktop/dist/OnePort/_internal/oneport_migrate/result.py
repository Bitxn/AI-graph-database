"""
Data models for check results.

CheckResult is the single object returned by Checker.check() and the
programmatic API. It serialises cleanly to JSON and is the source of truth
that all formatters render from.

The `blocking` flag is computed once, on the FULL finding set, at construction
time — and carried through `.filter()` unchanged. A display filter (e.g.
--min-severity critical) can therefore never hide a real error/critical from
the CI exit-code decision. Same semantics as oneport-review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
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


def compute_blocking(
    findings: list["Finding"], fail_on: "Severity | str" = Severity.ERROR
) -> bool:
    """True if any NON-WAIVED finding is at or above `fail_on`. Call on the FULL
    set only. `fail_on` defaults to ERROR (error + critical block), matching the
    historical behaviour; a team can loosen it to CRITICAL or tighten it to
    WARNING via config / --fail-on."""
    if isinstance(fail_on, str):
        fail_on = Severity(fail_on)
    return any(f.severity >= fail_on for f in findings if not f.waived)


@dataclass
class Location:
    """File location of a finding."""

    file: str
    line: int = 0


@dataclass
class Finding:
    """A single migration-safety finding."""

    rule_id: str
    severity: Severity
    message: str
    suggestion: str
    location: Location
    snippet: str = ""
    category: str = ""       # destructive | locking | reversibility | deploy
    framework: str = ""      # django | alembic | sql
    docs_url: str = ""
    # Repo-specific impact assessment written by the LLM layer ("the users
    # table is written on every request — a 4-minute lock here is an outage").
    # Empty when the LLM layer was skipped or had nothing to add.
    blast_radius: str = ""
    # An approved waiver keeps this finding in the report but stops it blocking CI.
    waived: bool = False
    waiver_reason: str = ""

    @property
    def file(self) -> str:
        return self.location.file

    @property
    def line(self) -> int:
        return self.location.line

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class CheckResult:
    """
    The complete result of a migration check run.

    Attributes:
        findings:      Findings, sorted by severity desc then file/line asc.
                       May be a display-filtered subset after `.filter()` — see `blocking`.
        target:        The path / PR URL / --staged / --head that was checked.
        files_checked: The migration files that were actually parsed.
        db:            The database dialect the rules ran against.
        model:         The model used for the LLM layer ("" when skipped).
        verdict:       Plain-English verdict paragraph from the LLM layer.
        rewrite_plan:  Ordered safe-rewrite steps from the LLM layer.
        llm_ok:        True if the LLM layer ran and parsed successfully.
        llm_note:      Why the LLM layer was skipped or degraded ("" when llm_ok).
        notes:         Parser honesty notes (e.g. regex SQL parsing limits hit).
        blocking:      True if the FULL (unfiltered) finding set contained an
                       error/critical. Fixed at construction, carried by .filter().
        diff:          Raw PR diff when the target was a PR (for --post anchoring).
        pr_ref:        {"owner","repo","number","head_sha"} for GitHub PR targets.
    """

    findings: list[Finding] = field(default_factory=list)
    target: str = ""
    files_checked: list[str] = field(default_factory=list)
    db: str = "postgres"
    model: str = ""
    verdict: str = ""
    rewrite_plan: list[str] = field(default_factory=list)
    llm_ok: bool = False
    llm_note: str = ""
    notes: list[str] = field(default_factory=list)
    total_tokens: int = 0
    elapsed_ms: int = 0
    blocking: bool = False
    diff: str = field(default="", repr=False)
    pr_ref: dict[str, Any] | None = None

    def filter(self, min_severity: Severity | str) -> "CheckResult":
        """Return a copy containing only findings at or above min_severity.

        `blocking`, `diff`, and `pr_ref` are carried over unchanged — blocking
        must reflect the full finding set regardless of the display filter.
        """
        if isinstance(min_severity, str):
            min_severity = Severity(min_severity)
        return CheckResult(
            findings=[f for f in self.findings if f.severity >= min_severity],
            target=self.target,
            files_checked=self.files_checked,
            db=self.db,
            model=self.model,
            verdict=self.verdict,
            rewrite_plan=self.rewrite_plan,
            llm_ok=self.llm_ok,
            llm_note=self.llm_note,
            notes=self.notes,
            total_tokens=self.total_tokens,
            elapsed_ms=self.elapsed_ms,
            blocking=self.blocking,
            diff=self.diff,
            pr_ref=self.pr_ref,
        )

    @property
    def critical(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.CRITICAL]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]

    @property
    def has_blocking_issues(self) -> bool:
        """Always reflects `blocking`, never recomputed from a possibly filtered list."""
        return self.blocking

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "files_checked": self.files_checked,
            "db": self.db,
            "model": self.model,
            "blocking": self.blocking,
            "verdict": self.verdict,
            "rewrite_plan": self.rewrite_plan,
            "llm_ok": self.llm_ok,
            "llm_note": self.llm_note,
            "notes": self.notes,
            "total_tokens": self.total_tokens,
            "elapsed_ms": self.elapsed_ms,
            "summary": {
                "total": len(self.findings),
                "critical": len(self.critical),
                "error": len(self.errors),
                "warning": len(self.warnings),
                "info": len([f for f in self.findings if f.severity == Severity.INFO]),
                "waived": len([f for f in self.findings if f.waived]),
            },
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def __len__(self) -> int:
        return len(self.findings)

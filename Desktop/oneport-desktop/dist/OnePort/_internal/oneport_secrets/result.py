"""
Core result types.

A `Finding` is one deterministic hit (regex or entropy) at a specific location.
Detection fills everything except `verdict`/`reason`/`remediation`; the LLM
triage layer fills `verdict`/`reason`, and the remediation layer fills the
rotation steps. The model never creates Findings — it only annotates them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    REAL = "real"
    FALSE_POSITIVE = "false_positive"
    UNREVIEWED = "unreviewed"  # detected but not yet triaged (fail-safe: blocks)


# Severity ordering for sorting / gating.
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass
class Finding:
    detector_id: str
    detector_name: str
    severity: str
    path: str
    line: int
    match: str  # the raw secret text — kept internal; use redacted() for output
    commit: str | None = None  # short SHA if from history; None if working tree/staged
    author: str = ""
    date: str = ""
    entropy: float | None = None
    line_text: str = ""  # the full source line (for the triage prompt / context)
    verdict: Verdict = Verdict.UNREVIEWED
    reason: str = ""
    remediation: list[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        """Stable id for a secret value — used to dedupe across commits."""
        return hashlib.sha256(self.match.encode("utf-8", "replace")).hexdigest()[:16]

    @property
    def location(self) -> str:
        where = f"{self.path}:{self.line}"
        if self.commit:
            where += f"@{self.commit[:8]}"
        return where

    @property
    def is_blocking(self) -> bool:
        """A finding blocks CI unless it was explicitly triaged as a false positive."""
        return self.verdict != Verdict.FALSE_POSITIVE

    def redacted(self, keep: int = 4) -> str:
        """Mask the middle of the secret so we never print it in full."""
        m = self.match
        if len(m) <= keep * 2 + 2:
            return (m[:2] + "...") if m else ""
        return f"{m[:keep]}...{m[-keep:]}"

    def masked_line(self) -> str:
        """The source line with the secret value replaced by its redaction."""
        if self.match and self.match in self.line_text:
            return self.line_text.replace(self.match, self.redacted())
        return self.line_text


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    scanned_files: int = 0
    scanned_commits: int = 0
    target: str = ""
    mode: str = "worktree"  # worktree | staged | history
    model: str = ""
    total_tokens: int = 0
    triaged: bool = False
    triage_capped: int = 0  # findings left UNREVIEWED because triage hit its cost cap
    # Why the LLM triage layer didn't run (rate limit, no tokens, auth). Detection
    # is deterministic and its findings stand on their own, so a triage failure
    # must degrade to "UNREVIEWED, still reported" — never to a silent scan.
    triage_error: str = ""

    def sorted_findings(self) -> list[Finding]:
        return sorted(
            self.findings,
            key=lambda f: (_SEV_ORDER.get(f.severity, 9), f.path, f.line),
        )

    @property
    def real(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict == Verdict.REAL]

    @property
    def false_positives(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict == Verdict.FALSE_POSITIVE]

    @property
    def blocking(self) -> list[Finding]:
        """Findings that fail the gate: real ones, plus untriaged (fail-safe)."""
        return [f for f in self.findings if f.is_blocking]

    @property
    def has_blocking_secrets(self) -> bool:
        return bool(self.blocking)


@dataclass
class EnvVar:
    name: str
    used_in: list[str] = field(default_factory=list)  # "path:line" refs in code
    declared: bool = False
    used: bool = False
    kind: str = "unknown"  # secret | toggle | unknown (LLM-classified)


@dataclass
class EnvResult:
    used_but_undeclared: list[EnvVar] = field(default_factory=list)
    declared_but_unused: list[EnvVar] = field(default_factory=list)
    example_path: str = ""
    scanned_files: int = 0
    model: str = ""
    total_tokens: int = 0
    classified: bool = False

    @property
    def has_drift(self) -> bool:
        return bool(self.used_but_undeclared or self.declared_but_unused)

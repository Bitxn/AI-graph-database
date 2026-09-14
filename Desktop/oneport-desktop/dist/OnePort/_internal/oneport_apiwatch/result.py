"""Data models for probe results.

Report is the single object the CLI renders and serialises. It is the source of
truth for the JSON output, the inline output, and the alerting payloads.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA = "oneport-apiwatch/v1"


@dataclass
class AssertionResult:
    """Outcome of one JSON-body assertion."""

    path: str
    op: str
    expected: Any
    actual: Any
    ok: bool


@dataclass
class Diagnosis:
    """AI explanation of a failure (populated only under --explain)."""

    summary: str
    likely_causes: list[str] = field(default_factory=list)
    suggested_action: str = ""
    model: str = ""
    # Set when the model call failed; the deterministic verdict is unaffected.
    error: str = ""


@dataclass
class CheckResult:
    """Deterministic outcome of probing one endpoint."""

    name: str
    url: str
    method: str
    ok: bool
    status_code: int | None = None
    latency_ms: float = 0.0
    expected_statuses: list[int] = field(default_factory=list)
    latency_budget_ms: int = 0
    # Transport-level error (DNS failure, connection refused, timeout).
    error: str = ""
    # Human-readable reasons the check failed. Empty when ok.
    failures: list[str] = field(default_factory=list)
    assertions: list[AssertionResult] = field(default_factory=list)
    # Truncated response body, kept for diagnosis context.
    body_snippet: str = ""
    diagnosis: Diagnosis | None = None
    # -- reliability / flap gating (set by gate.apply_gate) --
    attempts: int = 1                 # probe attempts made (1 + retries used)
    consecutive_failures: int = 0     # trailing failed runs incl. this one (history)
    threshold: int = 1               # consecutive failures needed to "trip"
    tripped: bool = False            # counts as failing for the gate/exit/alerts
    muted: bool = False              # under a maintenance mute — never trips
    mute_reason: str = ""
    newly_failing: bool = False      # tripped now, wasn't last run (fire an alert)
    recovered: bool = False          # was tripped last run, ok now (fire recovery)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.diagnosis is None:
            d.pop("diagnosis", None)
        return d


@dataclass
class Report:
    """Result of one full run over every check."""

    checks: list[CheckResult]
    generated_at: str = ""

    @property
    def ok(self) -> bool:
        """Raw: did every check pass THIS run (ignores flap gating/mutes)."""
        return all(c.ok for c in self.checks)

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]

    # -- gate view (what actually decides exit code + alerts) --
    @property
    def tripped(self) -> list[CheckResult]:
        """Checks that have failed enough consecutive runs to count as down."""
        return [c for c in self.checks if c.tripped]

    @property
    def gate_ok(self) -> bool:
        """True when nothing is tripped — the value the exit code uses. A single
        transient blip below the failure threshold does NOT make this False."""
        return not any(c.tripped for c in self.checks)

    @property
    def newly_failing(self) -> list[CheckResult]:
        return [c for c in self.checks if c.newly_failing]

    @property
    def recovered(self) -> list[CheckResult]:
        return [c for c in self.checks if c.recovered]

    @property
    def muted(self) -> list[CheckResult]:
        return [c for c in self.checks if c.muted]

    @property
    def summary(self) -> dict[str, int]:
        passed = sum(1 for c in self.checks if c.ok)
        return {"total": len(self.checks), "passed": passed, "failed": len(self.checks) - passed}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "generated_at": self.generated_at,
            "ok": self.ok,
            "gate_ok": self.gate_ok,
            "summary": {**self.summary, "tripped": len(self.tripped), "muted": len(self.muted)},
            "checks": [c.to_dict() for c in self.checks],
        }

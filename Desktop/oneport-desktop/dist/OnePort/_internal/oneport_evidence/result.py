"""
Result types.

A `GateRun` is one deterministic record of a gate executing (from the usage ledger
or an ingested op ship report). The report layer rolls these up, via a framework's
control map, into `ControlEvidence` and a final `EvidenceReport`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

# Ledger/gate statuses → whether the run represents an enforcement action.
_BLOCKING_STATUSES = {"fail", "error"}
_PASS_STATUSES = {"pass"}
_WARN_STATUSES = {"warn"}


@dataclass(frozen=True)
class GateRun:
    ts: int              # epoch seconds
    gate: str            # tool key, e.g. "secrets"
    status: str          # pass | warn | fail | error | skipped
    repo_hash: str = ""
    findings: int = 0    # known only for ingested reports (0 from the ledger)

    @property
    def blocked(self) -> bool:
        return self.status in _BLOCKING_STATUSES

    @property
    def passed(self) -> bool:
        return self.status in _PASS_STATUSES

    @property
    def ran(self) -> bool:
        return self.status != "skipped"


@dataclass
class ControlEvidence:
    control_id: str
    name: str
    description: str
    gates: list[str]              # oneport gates that evidence this control
    runs: int = 0
    passed: int = 0
    blocked: int = 0              # enforcement actions (violations caught)
    warned: int = 0
    last_run: int = 0             # epoch seconds

    @property
    def operating(self) -> bool:
        """A control is 'operating effectively' if its gates actually ran in-period."""
        return self.runs > 0

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.runs) if self.runs else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["operating"] = self.operating
        d["pass_rate"] = round(self.pass_rate, 4)
        return d


@dataclass
class GateSummary:
    gate: str
    runs: int
    passed: int
    blocked: int
    warned: int
    last_run: int


@dataclass
class EvidenceReport:
    org: str
    framework_id: str
    framework_name: str
    period_start: int             # epoch seconds
    period_end: int
    generated_at: int
    controls: list[ControlEvidence] = field(default_factory=list)
    gate_summaries: list[GateSummary] = field(default_factory=list)
    total_runs: int = 0
    deploys_gated: int = 0        # distinct ship events reconstructed
    blocked_deploys: int = 0
    repos: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def controls_operating(self) -> int:
        return sum(1 for c in self.controls if c.operating)

    def to_dict(self) -> dict[str, Any]:
        return {
            "org": self.org,
            "framework": {"id": self.framework_id, "name": self.framework_name},
            "period": {"start": self.period_start, "end": self.period_end},
            "generated_at": self.generated_at,
            "summary": {
                "total_gate_runs": self.total_runs,
                "deploys_gated": self.deploys_gated,
                "blocked_deploys": self.blocked_deploys,
                "repositories": self.repos,
                "controls_total": len(self.controls),
                "controls_operating": self.controls_operating,
            },
            "controls": [c.to_dict() for c in self.controls],
            "gates": [asdict(g) for g in self.gate_summaries],
            "notes": self.notes,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

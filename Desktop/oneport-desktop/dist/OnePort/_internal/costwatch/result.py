"""
Data models for Costwatch.

Resource     — one provisioned thing parsed deterministically from IaC.
Finding      — one waste finding from the LLM layer (saving + cheaper config).
CostReport   — the full result of an analyze run: resources, deterministic
               cost estimate, and findings. Serialises cleanly to JSON and is
               the single source of truth every formatter renders from.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """Waste severity — roughly, how much money is on the table / how clear-cut."""

    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "high": 2, "critical": 3}[self.value]

    def __ge__(self, other: Severity) -> bool:  # type: ignore[override]
        return self.rank >= other.rank

    def __gt__(self, other: Severity) -> bool:  # type: ignore[override]
        return self.rank > other.rank


@dataclass
class Resource:
    """A single provisioned resource extracted from IaC.

    `monthly_cost` is an approximate on-demand estimate from the bundled price
    table (see costwatch/pricing.py); None when the resource's size/type isn't
    in the table. `count` multiplies the per-unit cost (replicas, ASG desired
    capacity, num_cache_nodes, ...).
    """

    kind: str                       # e.g. "aws_instance", "google_compute_instance", "service"
    name: str                       # logical resource name
    provider: str                   # aws | gcp | docker
    file: str = ""
    line: int = 0
    size: str = ""                  # instance_type / instance_class / machine_type
    count: int = 1
    attributes: dict[str, Any] = field(default_factory=dict)
    monthly_cost: float | None = None   # per-unit; total is monthly_cost * count

    @property
    def total_monthly_cost(self) -> float:
        return (self.monthly_cost or 0.0) * max(self.count, 1)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_monthly_cost"] = round(self.total_monthly_cost, 2)
        if self.monthly_cost is not None:
            d["monthly_cost"] = round(self.monthly_cost, 2)
        return d


@dataclass
class Finding:
    """A single waste finding: what's oversized, why, and the cheaper config."""

    severity: Severity
    category: str                   # over-provisioned | always-on | oversized-volume |
                                    # missing-autoscaling | dev-no-shutdown | team-guideline
    resource: str                   # "aws_instance.api"
    message: str                    # why it's wasteful, one or two sentences
    current_config: str             # e.g. "instance_type = t3.2xlarge"
    suggested_config: str           # e.g. "instance_type = t3.medium"
    estimated_monthly_saving: float
    file: str = ""
    line: int = 0
    waived: bool = False            # approved via .oneport/costwatch-waivers.yml —
                                    # shown, but excluded from the CI gate.
    waiver_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["estimated_monthly_saving"] = round(self.estimated_monthly_saving, 2)
        return d


@dataclass
class CostReport:
    """The complete result of an analyze run."""

    path: str = ""
    resources: list[Resource] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    model: str = ""
    total_tokens: int = 0
    elapsed_ms: int = 0
    priced: bool = True             # False when no findings needed a model call
    # Set for a cost-diff (--post) run: extra provisioned cost this change adds.
    cost_delta: float | None = None
    notes: list[str] = field(default_factory=list)   # parser warnings, skipped files

    @property
    def total_monthly_cost(self) -> float:
        """Sum of deterministic per-resource estimates (priced resources only)."""
        return sum(r.total_monthly_cost for r in self.resources)

    @property
    def estimated_monthly_savings(self) -> float:
        return sum(f.estimated_monthly_saving for f in self.findings)

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    def by_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity == severity]

    def filter(self, min_severity: Severity | str) -> CostReport:
        """New report with only findings at or above min_severity (resources kept)."""
        if isinstance(min_severity, str):
            min_severity = Severity(min_severity)
        return CostReport(
            path=self.path,
            resources=self.resources,
            findings=[f for f in self.findings if f.severity >= min_severity],
            model=self.model,
            total_tokens=self.total_tokens,
            elapsed_ms=self.elapsed_ms,
            priced=self.priced,
            cost_delta=self.cost_delta,
            notes=self.notes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "model": self.model,
            "total_tokens": self.total_tokens,
            "elapsed_ms": self.elapsed_ms,
            "total_monthly_cost": round(self.total_monthly_cost, 2),
            "estimated_monthly_savings": round(self.estimated_monthly_savings, 2),
            "cost_delta": (round(self.cost_delta, 2) if self.cost_delta is not None else None),
            "summary": {
                "resources": len(self.resources),
                "findings": len(self.findings),
                "critical": len(self.by_severity(Severity.CRITICAL)),
                "high": len(self.by_severity(Severity.HIGH)),
                "warning": len(self.by_severity(Severity.WARNING)),
                "info": len(self.by_severity(Severity.INFO)),
                "waived": len([f for f in self.findings if f.waived]),
            },
            "resources": [r.to_dict() for r in self.resources],
            "findings": [f.to_dict() for f in self.findings],
            "notes": self.notes,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def __len__(self) -> int:
        return len(self.findings)


@dataclass
class GateResult:
    """The CI-gate decision for a report: whether it blocks, and why."""

    blocking: bool
    reasons: list[str] = field(default_factory=list)


def compute_gate(
    report: CostReport,
    fail_on: str | Severity | None = None,
    budget: float | None = None,
) -> GateResult:
    """Decide whether a report should fail CI.

    Two independent, opt-in conditions (either can trip the gate):
      * ``fail_on`` — block if any NON-WAIVED finding is at or above this
        severity (info | warning | high | critical).
      * ``budget``  — block if the deterministic total monthly cost exceeds this
        dollar cap. On a cost-diff (--post) run, the added cost (``cost_delta``)
        is what's compared, since that's what the change is responsible for.

    Both default to off, so the gate is silent until a team opts in — the
    historical behaviour (analyze always exits 0) is unchanged by default.
    Waived findings never contribute to the severity condition."""
    reasons: list[str] = []

    if fail_on:
        threshold = Severity(fail_on) if isinstance(fail_on, str) else fail_on
        hits = [f for f in report.findings if not f.waived and f.severity >= threshold]
        if hits:
            reasons.append(
                f"{len(hits)} finding(s) at or above '{threshold.value}' "
                f"(~${sum(f.estimated_monthly_saving for f in hits):,.0f}/mo of waste)"
            )

    if budget is not None:
        measured = report.cost_delta if report.cost_delta is not None else report.total_monthly_cost
        label = "added cost" if report.cost_delta is not None else "projected cost"
        if measured > budget:
            reasons.append(
                f"{label} ~${measured:,.0f}/mo exceeds budget ${budget:,.0f}/mo"
            )

    return GateResult(blocking=bool(reasons), reasons=reasons)

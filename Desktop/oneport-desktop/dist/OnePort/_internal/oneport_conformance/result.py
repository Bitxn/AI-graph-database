"""Result types for a conformance check."""

from __future__ import annotations

from dataclasses import dataclass, field

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}


@dataclass
class Deviation:
    rule_id: str          # which intent rule was broken (R3)
    rule_text: str        # the line the team wrote
    observed: str         # what the change actually does
    severity: str         # low | medium | high
    confidence: float     # 0..1 — how sure the model is this is a real breach
    explanation: str      # grounded reasoning
    file: str = ""
    line: int = 0
    waived: bool = False

    def sev_rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 2)


@dataclass
class ConformanceResult:
    deviations: list[Deviation] = field(default_factory=list)
    rules_checked: int = 0
    overall_confidence: float = 0.0
    model: str = ""
    tokens_used: int = 0
    notes: list[str] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""

    def active(self) -> list[Deviation]:
        return [d for d in self.deviations if not d.waived]

    def blocking(self, fail_on: str = "medium") -> list[Deviation]:
        """Un-waived deviations at or above the fail_on severity."""
        floor = SEVERITY_ORDER.get(fail_on, 2)
        return [d for d in self.active() if d.sev_rank() >= floor]

    def conforms(self, fail_on: str = "medium") -> bool:
        return not self.blocking(fail_on)

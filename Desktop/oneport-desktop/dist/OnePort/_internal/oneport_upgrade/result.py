"""
Result types.

A `Finding` is one deterministic hit of a rule at a location. `UpgradeReport`
bundles the findings for a run plus the (optional) LLM plan and verification
outcome, and serialises to the Oneport-style JSON contract.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from oneport_upgrade.rules.schema import REMOVED


@dataclass
class Finding:
    rule_id: str
    title: str
    severity: str            # "removed" | "deprecated"
    file: str
    line: int
    col: int
    snippet: str
    replacement: str = ""
    auto: bool = False
    hint: str = ""
    applied: bool = False    # set True by the codemod engine when rewritten
    waived: bool = False     # approved via .oneport/upgrade-waivers.yml — shown,
                             # but excluded from the gate and left un-rewritten.
    waiver_reason: str = ""

    @property
    def gate_severity(self) -> str:
        # removed → hard break on the target runtime; deprecated → warning.
        return "error" if self.severity == REMOVED else "warning"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gate_severity"] = self.gate_severity
        return d


@dataclass
class VerifyResult:
    ran: bool = False
    before_passed: int = 0
    before_failed: int = 0
    after_passed: int = 0
    after_failed: int = 0
    note: str = ""

    @property
    def regressed(self) -> bool:
        return self.ran and self.after_failed > self.before_failed


@dataclass
class UpgradeReport:
    migration: str
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    model: str = ""
    total_tokens: int = 0
    elapsed_ms: int = 0
    plan: list[str] = field(default_factory=list)   # LLM ordered steps
    verify: VerifyResult | None = None
    notes: list[str] = field(default_factory=list)

    # ── rollups ──────────────────────────────────────────────────────────────
    @property
    def removed(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == REMOVED]

    @property
    def blocking_removed(self) -> list[Finding]:
        """Removed-API findings that still count toward the CI gate (not waived)."""
        return [f for f in self.removed if not f.waived]

    @property
    def waived(self) -> list[Finding]:
        return [f for f in self.findings if f.waived]

    @property
    def auto_fixable(self) -> list[Finding]:
        return [f for f in self.findings if f.auto and not f.applied]

    @property
    def applied(self) -> list[Finding]:
        return [f for f in self.findings if f.applied]

    @property
    def manual(self) -> list[Finding]:
        return [f for f in self.findings if not f.auto and not f.applied]

    def counts(self) -> dict[str, int]:
        return {
            "total": len(self.findings),
            "removed": len(self.removed),
            "deprecated": len(self.findings) - len(self.removed),
            "auto_fixable": len(self.auto_fixable),
            "applied": len(self.applied),
            "manual": len(self.manual),
            "waived": len(self.waived),
        }

    @property
    def files_touched(self) -> list[str]:
        return sorted({f.file for f in self.applied})

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration": self.migration,
            "model": self.model,
            "total_tokens": self.total_tokens,
            "elapsed_ms": self.elapsed_ms,
            "files_scanned": self.files_scanned,
            "counts": self.counts(),
            "plan": self.plan,
            "verify": asdict(self.verify) if self.verify else None,
            "notes": self.notes,
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

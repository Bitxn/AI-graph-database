"""
Result types.

A `Change` is a deterministic fact found by the AST/spec differ — what changed,
where, with old/new signatures. The LLM layer only fills in the verdict
narrative (`impact`, `migration`) and may adjust the verdict; the detection
itself is never model-driven.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    BREAKING = "BREAKING"
    RISKY = "RISKY"
    COMPATIBLE = "COMPATIBLE"


class ChangeKind(str, Enum):
    # Python surface changes
    SYMBOL_REMOVED = "symbol_removed"
    SYMBOL_ADDED = "symbol_added"
    PARAM_REMOVED = "param_removed"
    PARAM_ADDED_REQUIRED = "param_added_required"
    PARAM_ADDED_OPTIONAL = "param_added_optional"
    PARAM_RENAMED = "param_renamed"
    PARAMS_REORDERED = "params_reordered"
    DEFAULT_CHANGED = "default_changed"
    DEFAULT_REMOVED = "default_removed"
    ANNOTATION_CHANGED = "annotation_changed"
    RETURN_ANNOTATION_CHANGED = "return_annotation_changed"
    SYNC_TO_ASYNC = "sync_to_async"
    ASYNC_TO_SYNC = "async_to_sync"
    # Module-level constants (public UPPER/typed names)
    CONSTANT_REMOVED = "constant_removed"
    CONSTANT_ADDED = "constant_added"
    CONSTANT_TYPE_CHANGED = "constant_type_changed"
    CONSTANT_VALUE_CHANGED = "constant_value_changed"
    # Enum members
    ENUM_MEMBER_REMOVED = "enum_member_removed"
    ENUM_MEMBER_ADDED = "enum_member_added"
    ENUM_MEMBER_VALUE_CHANGED = "enum_member_value_changed"
    # Dataclass / pydantic-model fields
    FIELD_REMOVED = "field_removed"
    FIELD_ADDED_REQUIRED = "field_added_required"
    FIELD_ADDED_OPTIONAL = "field_added_optional"
    FIELD_TYPE_CHANGED = "field_type_changed"
    # Package re-exports (names surfaced through __init__.py)
    REEXPORT_REMOVED = "reexport_removed"
    REEXPORT_ADDED = "reexport_added"
    # A public symbol moved between modules (import path changed)
    SYMBOL_RELOCATED = "symbol_relocated"
    # OpenAPI spec changes
    ENDPOINT_REMOVED = "endpoint_removed"
    ENDPOINT_ADDED = "endpoint_added"
    RESPONSE_REMOVED = "response_removed"
    RESPONSE_SCHEMA_CHANGED = "response_schema_changed"


# What each kind means for consumers before the model says a word. The LLM may
# refine these (e.g. downgrade per team guidelines) but never invents changes.
DEFAULT_VERDICTS: dict[ChangeKind, Verdict] = {
    ChangeKind.SYMBOL_REMOVED: Verdict.BREAKING,
    ChangeKind.SYMBOL_ADDED: Verdict.COMPATIBLE,
    ChangeKind.PARAM_REMOVED: Verdict.BREAKING,
    ChangeKind.PARAM_ADDED_REQUIRED: Verdict.BREAKING,
    ChangeKind.PARAM_ADDED_OPTIONAL: Verdict.COMPATIBLE,
    ChangeKind.PARAM_RENAMED: Verdict.BREAKING,
    ChangeKind.PARAMS_REORDERED: Verdict.BREAKING,
    ChangeKind.DEFAULT_CHANGED: Verdict.RISKY,
    ChangeKind.DEFAULT_REMOVED: Verdict.BREAKING,
    ChangeKind.ANNOTATION_CHANGED: Verdict.RISKY,
    ChangeKind.RETURN_ANNOTATION_CHANGED: Verdict.RISKY,
    ChangeKind.SYNC_TO_ASYNC: Verdict.BREAKING,
    ChangeKind.ASYNC_TO_SYNC: Verdict.BREAKING,
    ChangeKind.CONSTANT_REMOVED: Verdict.BREAKING,
    ChangeKind.CONSTANT_ADDED: Verdict.COMPATIBLE,
    ChangeKind.CONSTANT_TYPE_CHANGED: Verdict.RISKY,
    ChangeKind.CONSTANT_VALUE_CHANGED: Verdict.RISKY,
    ChangeKind.ENUM_MEMBER_REMOVED: Verdict.BREAKING,
    ChangeKind.ENUM_MEMBER_ADDED: Verdict.COMPATIBLE,
    ChangeKind.ENUM_MEMBER_VALUE_CHANGED: Verdict.RISKY,
    ChangeKind.FIELD_REMOVED: Verdict.BREAKING,
    ChangeKind.FIELD_ADDED_REQUIRED: Verdict.BREAKING,
    ChangeKind.FIELD_ADDED_OPTIONAL: Verdict.COMPATIBLE,
    ChangeKind.FIELD_TYPE_CHANGED: Verdict.RISKY,
    ChangeKind.REEXPORT_REMOVED: Verdict.BREAKING,
    ChangeKind.REEXPORT_ADDED: Verdict.COMPATIBLE,
    ChangeKind.SYMBOL_RELOCATED: Verdict.RISKY,
    ChangeKind.ENDPOINT_REMOVED: Verdict.BREAKING,
    ChangeKind.ENDPOINT_ADDED: Verdict.COMPATIBLE,
    ChangeKind.RESPONSE_REMOVED: Verdict.BREAKING,
    ChangeKind.RESPONSE_SCHEMA_CHANGED: Verdict.RISKY,
}


@dataclass
class CallerRef:
    """One internal usage site of a changed symbol (grep-based, deterministic)."""

    file: str
    line: int
    snippet: str

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "snippet": self.snippet}


@dataclass
class Change:
    """One deterministic API-surface change."""

    id: int
    kind: ChangeKind
    file: str
    line: int  # head-file line of the changed symbol (base line if removed)
    symbol: str  # qualname, e.g. "charge" or "Client.request"; endpoint for spec changes
    detail: str  # human sentence stating the fact, e.g. 'required param "timeout" removed'
    old_signature: str = ""
    new_signature: str = ""
    verdict: Verdict = Verdict.RISKY
    impact: str = ""  # LLM: one-line consumer impact
    migration: str = ""  # LLM: migration note
    callers: list[CallerRef] = field(default_factory=list)
    # A matching, unexpired waiver acknowledges this break: it stays in the
    # report but no longer fails the gate.
    waived: bool = False
    waiver_reason: str = ""

    @property
    def gating_verdict(self) -> Verdict:
        """Verdict for gate math: a waived change never blocks."""
        return Verdict.COMPATIBLE if self.waived else self.verdict

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "file": self.file,
            "line": self.line,
            "symbol": self.symbol,
            "detail": self.detail,
            "old_signature": self.old_signature,
            "new_signature": self.new_signature,
            "verdict": self.verdict.value,
            "impact": self.impact,
            "migration": self.migration,
            "waived": self.waived,
            "waiver_reason": self.waiver_reason,
            "callers": [c.to_dict() for c in self.callers],
        }


@dataclass
class ApiDiffResult:
    changes: list[Change] = field(default_factory=list)
    target: str = ""
    model: str = ""
    llm_used: bool = False
    total_tokens: int = 0
    elapsed_ms: int = 0
    files_checked: int = 0
    # Set when target is a PR URL: {"owner", "repo", "number", "head_sha", "diff"}
    pr_ref: dict | None = None

    @property
    def breaking(self) -> list[Change]:
        return [c for c in self.changes if c.verdict == Verdict.BREAKING]

    @property
    def risky(self) -> list[Change]:
        return [c for c in self.changes if c.verdict == Verdict.RISKY]

    @property
    def compatible(self) -> list[Change]:
        return [c for c in self.changes if c.verdict == Verdict.COMPATIBLE]

    @property
    def waived(self) -> list[Change]:
        return [c for c in self.changes if c.waived]

    @property
    def has_breaking(self) -> bool:
        """Gate signal: a BREAKING change that has NOT been waived."""
        return any(c.gating_verdict == Verdict.BREAKING for c in self.changes)

    @property
    def verdict(self) -> str:
        """Overall gate verdict — waived breaks don't count against the gate."""
        gating = [c.gating_verdict for c in self.changes]
        if Verdict.BREAKING in gating:
            return Verdict.BREAKING.value
        if Verdict.RISKY in gating:
            return Verdict.RISKY.value
        return Verdict.COMPATIBLE.value

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "verdict": self.verdict,
            "model": self.model,
            "llm_used": self.llm_used,
            "total_tokens": self.total_tokens,
            "elapsed_ms": self.elapsed_ms,
            "files_checked": self.files_checked,
            "counts": {
                "breaking": len(self.breaking),
                "risky": len(self.risky),
                "compatible": len(self.compatible),
                "waived": len(self.waived),
            },
            "changes": [c.to_dict() for c in self.changes],
        }

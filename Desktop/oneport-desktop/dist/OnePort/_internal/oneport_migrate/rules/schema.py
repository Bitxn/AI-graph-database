"""Pydantic models for rule definitions."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel


class RuleSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Rule(BaseModel):
    id: str                    # e.g. "OPM001"
    description: str           # Short human-readable description
    category: str              # destructive | locking | reversibility | deploy
    severity: RuleSeverity     # Default severity
    rationale: str = ""        # Why this rule exists
    docs_url: str = ""         # Link to full documentation

    class Config:
        use_enum_values = True

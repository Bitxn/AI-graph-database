"""Pydantic models for rule definitions."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class RuleSeverity(str, Enum):
    INFO     = "info"
    WARNING  = "warning"
    ERROR    = "error"
    CRITICAL = "critical"


class Rule(BaseModel):
    id: str                              # e.g. "OPR001"
    description: str                     # Short human-readable description
    category: str                        # security | performance | logic | style | pattern
    severity: RuleSeverity               # Default severity
    rationale: str = ""                  # Why this rule exists
    docs_url: str = ""                   # Link to full documentation
    examples: list[str] = Field(default_factory=list)   # Bad code examples

    class Config:
        use_enum_values = True

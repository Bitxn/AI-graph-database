"""
Loads and merges the rule set from base_rules.yaml with project-level overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from oneport.rules.schema import Rule


BASE_RULES_PATH = Path(__file__).parent / "base_rules.yaml"


@dataclass
class RuleSet:
    all_rules: list[Rule] = field(default_factory=list)
    ignored_ids: set[str] = field(default_factory=set)
    severity_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def active_rules(self) -> list[Rule]:
        return [r for r in self.all_rules if r.id not in self.ignored_ids]


def load_rule_set(
    ignored_ids: list[str] | None = None,
    severity_overrides: dict[str, str] | None = None,
) -> RuleSet:
    """
    Load the base rule set and apply project-level overrides.

    Args:
        ignored_ids:        Rule IDs to suppress entirely.
        severity_overrides: Map of rule_id → new severity.
    """
    raw: list[dict[str, Any]] = _load_base_rules()
    rules = [Rule(**item) for item in raw]

    return RuleSet(
        all_rules=rules,
        ignored_ids=set(ignored_ids or []),
        severity_overrides=severity_overrides or {},
    )


def _load_base_rules() -> list[dict[str, Any]]:
    with BASE_RULES_PATH.open() as f:
        data = yaml.safe_load(f)
    return data.get("rules", [])

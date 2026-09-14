"""
Builds the system prompt and user message for the blast-radius call.

Kept separate from Advisor so it can be tested in isolation and swapped
without touching orchestration logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from oneport_migrate.operations import ParsedMigration
from oneport_migrate.result import Finding


SYSTEM_PROMPT = ""
# NOTE: the tuned system prompt for this call lives SERVER-SIDE in the
# Oneport prompt vault, keyed by tool:action — the proxy injects it at
# call time and ignores this placeholder. The prompt is intentionally
# not shipped in this package.



@dataclass
class PromptParts:
    system: str
    user: str


def build_prompt(
    findings: list[Finding],
    migrations: list[ParsedMigration],
    db: str,
    repo_context: str = "",
    guidelines: str = "",
    max_context_chars: int = 24_000,
) -> PromptParts:
    """Assemble the system + user messages for a blast-radius call."""
    findings_payload = [
        {
            "rule_id": f.rule_id,
            "severity": f.severity.value,
            "category": f.category,
            "file": f.file,
            "line": f.line,
            "message": f.message,
        }
        for f in findings
    ]

    parts: list[str] = [f"Database dialect: {db}"]

    parts.append(
        "Rule-engine findings (fixed — assess, do not re-litigate):\n"
        + json.dumps(findings_payload, indent=2)
    )

    budget = max_context_chars
    sources: list[str] = []
    for m in migrations:
        block = f"=== migration: {m.file} ({m.framework}) ===\n{m.source.strip()}"
        if len(block) > budget:
            block = block[:budget] + "\n... (truncated)"
        budget -= len(block)
        sources.append(block)
        if budget <= 0:
            break
    parts.append("Migration file(s):\n\n" + "\n\n".join(sources))

    if repo_context:
        if len(repo_context) > budget > 0:
            repo_context = repo_context[:budget] + "\n... (truncated)"
        parts.append(
            "Repo context (models/schema near the migrations — use this to judge "
            "how hot each table is):\n\n" + repo_context
        )
    else:
        parts.append(
            "Repo context: none available. Say so where it limits your assessment."
        )

    if guidelines:
        parts.append(
            "Team guidelines (the ONLY basis for severity_adjustments):\n" + guidelines
        )
    else:
        parts.append(
            "Team guidelines: none. severity_adjustments MUST be an empty list."
        )

    return PromptParts(system=SYSTEM_PROMPT, user="\n\n".join(parts))

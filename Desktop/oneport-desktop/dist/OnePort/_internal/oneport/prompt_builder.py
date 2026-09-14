"""
Builds the system prompt and user message for a review call.

Kept separate from Reviewer so it can be tested in isolation and swapped
without touching orchestration logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from oneport.rules.loader import RuleSet
from oneport.rules.schema import Rule


SYSTEM_PROMPT = ""
# NOTE: the tuned system prompt for this call lives SERVER-SIDE in the
# Oneport prompt vault, keyed by tool:action — the proxy injects it at
# call time and ignores this placeholder. The prompt is intentionally
# not shipped in this package.



@dataclass
class PromptParts:
    system: str
    user: str


def _format_rule_catalog(rules: list[Rule]) -> str:
    """
    Render the active rule catalog into the system prompt.

    Without this, the model invents its own rule_id strings loosely anchored to
    the single "OPR001" example in the output-format schema — so a user's
    `.oneportrc` `rules.ignore` / `rules.severity` (keyed by rule_id) only take
    effect if the model happens to emit a matching ID. Showing the real catalog
    makes rule_id usage — and therefore ignore/severity-override config —
    actually reliable.
    """
    lines = ["Known rule catalog — when a finding matches one of these, use its exact rule_id:"]
    for rule in rules:
        severity = getattr(rule.severity, "value", rule.severity)
        lines.append(f"  {rule.id} · {rule.category}/{severity} — {rule.description}")
    return "\n".join(lines)


def build_prompt(
    diff: str,
    file_name: str = "",
    rule_set: RuleSet | None = None,
    extra_context: str = "",
    guidelines: str = "",
) -> PromptParts:
    """
    Assemble the system + user messages for a review call.

    Args:
        diff:          The unified diff or full file content to review.
        file_name:     The primary file name (for single-file reviews).
        rule_set:      Active rule set. Ignored rules are listed so the model skips them.
        extra_context: Any additional context (e.g. PR title + description).
        guidelines:    Team guidelines markdown (see oneport/guidelines.py),
                       enforced like the rule catalog.
    """
    # The tuned instructions live server-side (prompt vault, injected by the
    # proxy). Everything below is per-repo DATA — the rule catalog, team
    # guidelines, and overrides — which must travel with the request, so it
    # goes in the USER block, not the (vault-overridden) system prompt.
    system = SYSTEM_PROMPT

    data_blocks: list[str] = []
    if rule_set and rule_set.active_rules:
        data_blocks.append(_format_rule_catalog(rule_set.active_rules))

    if guidelines:
        data_blocks.append(
            "Team guidelines — project-specific rules this team has taught you. "
            "Enforce them exactly like the rule catalog above (report violations "
            'with rule_id "OPR000" and category "team-guideline"):\n'
            + guidelines
        )

    if rule_set and rule_set.ignored_ids:
        ignored_rules = [r for r in rule_set.all_rules if r.id in rule_set.ignored_ids]
        if ignored_rules:
            lines = ["Do NOT report any issue matching these disabled rules (project config):"]
            lines += [f"  {rule.id} — {rule.description}" for rule in ignored_rules]
            data_blocks.append("\n".join(lines))

    if rule_set and rule_set.severity_overrides:
        overrides_json = json.dumps(rule_set.severity_overrides, indent=2)
        data_blocks.append(f"Severity overrides for these rule IDs:\n{overrides_json}")

    parts = []
    if data_blocks:
        parts.append("\n\n".join(data_blocks) + "\n")
    if file_name:
        parts.append(f"File: {file_name}\n")
    if extra_context:
        parts.append(f"Context:\n{extra_context}\n")
    parts.append("Code to review:\n```\n" + diff.strip() + "\n```")

    return PromptParts(system=system, user="\n".join(parts))


def build_pr_prompt(
    diff: str,
    title: str = "",
    description: str = "",
    rule_set: RuleSet | None = None,
    guidelines: str = "",
    extra_context: str = "",
) -> PromptParts:
    """Variant for PR reviews where we have title + description as context."""
    context_parts = []
    if title:
        context_parts.append(f"PR Title: {title}")
    if description:
        context_parts.append(f"PR Description:\n{description}")
    if extra_context:
        context_parts.append(extra_context)
    extra_context = "\n".join(context_parts)

    return build_prompt(
        diff=diff,
        rule_set=rule_set,
        extra_context=extra_context,
        guidelines=guidelines,
    )

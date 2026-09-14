"""
The narrator — the ONLY place a model is used.

Input: the deterministic Commits. Output: a headline + themed groups of bullets in
the voice of the requested mode (standup / weekly / release notes). Hard rule: it
may only rephrase and group what the commits say — it must not invent work, numbers,
or tickets. If it returns malformed output, we fall back to the deterministic
grouping (see formatters), so a bad model reply never loses the report.
"""

from __future__ import annotations

import json
import re

from oneport_standup.config import Config
from oneport_standup.exceptions import StandupError
from oneport_standup.llm import call_llm
from oneport_standup.result import Commit, Group, Narrative

_VOICE = {
    "daily": "a first-person daily standup update: terse, past tense, what got done. "
             "One short headline, then 1-2 groups (e.g. 'Shipped', 'In progress').",
    "weekly": "a professional weekly summary for a manager or teammate: grouped by "
              "theme/area, outcome-focused, no filler.",
    "since": "a concise progress summary grouped by theme.",
    "release": "release notes / a changelog: group strictly as 'Added', 'Fixed', "
               "'Changed', 'Removed'. Each bullet is user-facing and imperative.",
}


def _system(mode: str) -> str:
    return (
        "You write engineering status updates from git commits. You are given the "
        "actual commits (subjects + stats). Produce " + _VOICE.get(mode, _VOICE["since"]) +
        "\n\nHARD RULES: use ONLY what the commits state. Do NOT invent features, "
        "numbers, or tickets. Merge related commits into one clear bullet. Keep "
        "bullets short. Preserve ticket/PR refs (ABC-123, #42) when present.\n\n"
        'Reply with ONLY this JSON (no prose, no fences):\n'
        '{"headline": "<one line>", "groups": [{"title": "<group>", '
        '"bullets": ["<bullet>", "..."]}]}'
    )


def _commits_block(commits: list[Commit], cap: int) -> str:
    lines = []
    for c in commits[:cap]:
        refs = " ".join(c.tickets + c.prs)
        refs = f"  [{refs}]" if refs else ""
        lines.append(f"- {c.subject} ({c.files}f +{c.insertions}/-{c.deletions}){refs}")
    if len(commits) > cap:
        lines.append(f"- (+{len(commits) - cap} more commits)")
    return "\n".join(lines)


def narrate(commits: list[Commit], mode: str, config: Config, guidelines: str = "") -> tuple[Narrative, int]:
    """Return (Narrative, tokens). Empty Narrative (tokens 0) when skipped/failed —
    the caller then renders the deterministic fallback."""
    if not config.has_key or not commits:   # has_key == logged in to Oneport
        return Narrative(), 0
    user = _commits_block(commits, config.max_commits)
    if guidelines:
        user += f"\n\nTeam tone/context:\n{guidelines[:1000]}"
    try:
        text, tokens = call_llm(config.model, config.api_key, _system(mode), user, config.max_tokens)
        data = _parse(text)
    except (StandupError, ValueError, json.JSONDecodeError):
        return Narrative(), 0
    groups = [
        Group(title=str(g.get("title", "")).strip(),
              bullets=[str(b).strip() for b in g.get("bullets", []) if str(b).strip()])
        for g in data.get("groups", []) if isinstance(g, dict)
    ]
    groups = [g for g in groups if g.bullets]
    return Narrative(headline=str(data.get("headline", "")).strip(), groups=groups), tokens


def _parse(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(0)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data

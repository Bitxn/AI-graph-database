"""
LLM risk ranking — judgment on top of the deterministic gaps.

The model NEVER computes coverage: it receives gaps that are already proven
uncovered (diff ∩ coverage.xml) and only decides how much each one matters.
Payment, auth, and data-write logic outrank logging and cosmetics; team
guidelines can shift the ranking further.
"""

from __future__ import annotations

import json
import re

from oneport_testgap.config import Config
from oneport_testgap.exceptions import ParseError
from oneport_testgap.gaps import RISK_ORDER, Gap, Risk
from oneport_testgap.llm import complete

RANK_SYSTEM_PROMPT = """\
You are a senior test engineer triaging untested code changes before a release.

You receive a numbered list of GAPS. Each gap is code that was changed in the
current diff and was NEVER EXECUTED by the project's test suite — that is an
established fact from coverage data, not your judgment. Your job is ONLY to
rank how risky it is to ship each gap untested, and to say why in one sentence.

Risk levels:
  critical  money movement, payments, auth/authz, security checks, data writes
            or deletes, migrations, anything that corrupts state if wrong
  high      business logic whose wrong answer reaches users or other systems
  medium    input parsing, validation, formatting with user-visible effects
  low       logging, debug output, comments-adjacent code, cosmetic branches

Respond with STRICT JSON only — no markdown fences, no prose:
{"rankings": [{"id": 0, "risk": "critical", "why": "one sentence"}, ...]}

Every gap id you were given MUST appear exactly once in "rankings".
"""


def rank_gaps(gaps: list[Gap], config: Config, guidelines: str = "") -> tuple[list[Gap], int]:
    """
    Ask the model to assign a risk level and one-sentence rationale per gap.

    Returns (gaps sorted most-risky-first, total_tokens). Gaps the model fails
    to mention stay UNRANKED and sort last — they're never silently dropped.
    """
    if not gaps:
        return [], 0

    user_prompt = _build_user_prompt(gaps, guidelines)
    text, tokens = complete(
        model=config.model,
        api_key=config.api_key,
        system=RANK_SYSTEM_PROMPT,
        user=user_prompt,
        max_tokens=config.max_tokens,
    )

    rankings = _parse_rankings(text)
    for i, gap in enumerate(gaps):
        if i in rankings:
            risk, why = rankings[i]
            gap.risk = risk
            gap.why = why

    gaps.sort(key=lambda g: (RISK_ORDER[g.risk], g.file, g.first_line))
    return gaps, tokens


def _build_user_prompt(gaps: list[Gap], guidelines: str) -> str:
    parts: list[str] = []
    if guidelines:
        parts.append(
            "TEAM GUIDELINES (testing rules here override the defaults):\n"
            f"{guidelines}\n"
        )
    parts.append("GAPS (changed lines with zero test coverage):\n")
    for i, gap in enumerate(gaps):
        measured_note = "" if gap.measured else " [file not measured by coverage at all]"
        parts.append(
            f"--- gap id={i} ---\n"
            f"file: {gap.file}\n"
            f"function: {gap.function}\n"
            f"uncovered changed lines: {gap.line_ranges}{measured_note}\n"
            f"code ('>' marks the uncovered changed lines):\n{gap.snippet}\n"
        )
    return "\n".join(parts)


def _parse_rankings(text: str) -> dict[int, tuple[Risk, str]]:
    data = _extract_json(text)
    rankings = data.get("rankings")
    if not isinstance(rankings, list):
        raise ParseError("Model response has no 'rankings' list.", raw_response=text)

    result: dict[int, tuple[Risk, str]] = {}
    for entry in rankings:
        if not isinstance(entry, dict):
            continue
        try:
            gap_id = int(entry["id"])
            risk = Risk(str(entry.get("risk", "")).lower())
        except (KeyError, ValueError):
            continue  # one malformed entry shouldn't sink the run
        if risk == Risk.UNRANKED:
            continue
        result[gap_id] = (risk, str(entry.get("why", "")).strip())
    return result


def _extract_json(text: str) -> dict:
    """Parse model output as JSON, tolerating ```json fences and stray prose."""
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    if not candidate.startswith("{"):
        brace = candidate.find("{")
        if brace >= 0:
            candidate = candidate[brace:candidate.rfind("}") + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ParseError(f"Model returned unparseable JSON: {exc}", raw_response=text) from exc
    if not isinstance(data, dict):
        raise ParseError("Model returned JSON that isn't an object.", raw_response=text)
    return data

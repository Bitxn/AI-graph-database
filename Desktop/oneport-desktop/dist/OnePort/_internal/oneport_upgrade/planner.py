"""
The planner — the only place a model is used.

Given the deterministic findings (grouped by rule, with counts and which are
auto-fixable), it writes an ordered, human migration plan: what to run first, what
needs manual attention, and what to watch when testing. It never invents findings —
it only sequences and explains the ones the scanner produced. Fails soft: on any
error the report simply has no plan, and scan/apply are unaffected.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from oneport_account import is_logged_in

from oneport_upgrade.config import Config
from oneport_upgrade.exceptions import UpgradeError
from oneport_upgrade.llm import call_llm
from oneport_upgrade.result import Finding

SYSTEM_PROMPT = """\
You are a senior engineer planning a framework/version upgrade. You are given the
deterministic findings from a scanner: each rule, how many hits, whether it can be
auto-fixed, and its guidance. Produce a short, ORDERED migration plan.

Rules: use ONLY the findings given — do not invent APIs or steps. Put safe
auto-fixable renames first (they're low-risk), then the manual/structural changes,
then a final 'verify' step. Keep each step to one line.

Reply with ONLY this JSON (no prose, no fences):
{"plan": ["step 1", "step 2", "..."]}
"""


def _facts(migration: str, findings: list[Finding]) -> str:
    by_rule: dict[str, list[Finding]] = {}
    for f in findings:
        by_rule.setdefault(f.rule_id, []).append(f)
    lines = [f"Target migration: {migration}", f"{len(findings)} findings across "
             f"{len({f.file for f in findings})} files.", ""]
    for rid, group in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        g0 = group[0]
        tag = "auto-fixable" if g0.auto else "MANUAL"
        lines.append(f"- {g0.title} — {len(group)} hit(s), {g0.severity}, {tag}"
                     + (f". {g0.hint}" if g0.hint else ""))
    return "\n".join(lines)


def make_plan(migration: str, findings: list[Finding], config: Config, guidelines: str = "") -> tuple[list[str], int]:
    # The plan step is the only metered path. Gate on being logged in to Oneport
    # (managed keys) — NOT on config.api_key, which is always "" now that keys are
    # server-side. scan/apply stay fully local and need no account.
    if not findings or not is_logged_in():
        return [], 0
    user = _facts(migration, findings)
    if guidelines:
        user += f"\n\nTeam guidelines:\n{guidelines[:1000]}"
    try:
        text, tokens = call_llm(config.model, config.api_key, SYSTEM_PROMPT, user, config.max_tokens)
        data = _parse(text)
    except (UpgradeError, ValueError, json.JSONDecodeError):
        return [], 0
    plan = [str(s).strip() for s in data.get("plan", []) if str(s).strip()]
    return plan, tokens


def _parse(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(0)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("expected object")
    return data

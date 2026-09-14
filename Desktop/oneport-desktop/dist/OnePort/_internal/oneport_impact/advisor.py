"""
The risk-verdict layer — judgment, and only judgment.

Input to the model: the deterministic facts about one target (fan-in, the caller
files, the co-change partners with confidences, the owners) plus optional team
guidelines. Output: a short plain-English risk read and the single consumer most
worth checking. The model never adds a caller or partner — if it names something
not in the facts, we ignore it.

Skipped cleanly (leaving facts-only output) when there's no key or --no-llm.
"""

from __future__ import annotations

import json
import re

from oneport_impact.config import Config
from oneport_impact.exceptions import ImpactError
from oneport_impact.llm import call_llm
from oneport_impact.result import BlastRadius

SYSTEM_PROMPT = ""
# NOTE: the tuned system prompt for this call lives SERVER-SIDE in the
# Oneport prompt vault, keyed by tool:action — the proxy injects it at
# call time and ignores this placeholder. The prompt is intentionally
# not shipped in this package.



def _facts_block(br: BlastRadius, guidelines: str) -> str:
    lines = [
        f"Target: {br.target}  ({br.target_kind})",
        f"Fan-in: {br.fan_in} call site(s) across {br.caller_files} file(s)",
        f"History: the target file(s) changed in {br.target_commits} commit(s)",
        "",
        "Top calling files:",
    ]
    caller_files: dict[str, int] = {}
    for c in br.callers:
        caller_files[c.file] = caller_files.get(c.file, 0) + 1
    for f, n in sorted(caller_files.items(), key=lambda x: -x[1])[:10]:
        lines.append(f"  - {f} ({n} call site(s))")
    if not caller_files:
        lines.append("  (none found — no static callers)")

    lines += ["", "Historically co-changed files (temporal coupling):"]
    for p in br.partners[:10]:
        lines.append(f"  - {p.path}  ({p.pct}% of the target's commits, {p.together}/{p.target_commits})")
    if not br.partners:
        lines.append("  (none)")

    if br.owners:
        lines += ["", "Owners: " + ", ".join(
            (o.name + (" [CODEOWNER]" if o.codeowner else "")) for o in br.owners)]
    if guidelines:
        lines += ["", "Team guidelines (context; do not change the JSON schema):", guidelines[:1500]]
    return "\n".join(lines)


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


def assess(br: BlastRadius, config: Config, guidelines: str = "") -> int:
    """Fill br.verdict/riskiest/reason in place. Returns tokens used (0 on skip)."""
    if not config.has_key:   # has_key == logged in to Oneport
        return 0
    user = _facts_block(br, guidelines)
    try:
        text, tokens = call_llm(config.model, config.api_key, SYSTEM_PROMPT, user, config.max_tokens)
        data = _parse(text)
    except (ImpactError, ValueError, json.JSONDecodeError) as exc:
        br.reason = f"(risk verdict unavailable: {str(exc)[:120]})"
        return 0
    br.verdict = str(data.get("verdict", "")).strip()
    br.riskiest = str(data.get("riskiest", "")).strip()
    br.reason = str(data.get("reason", "")).strip()
    return tokens

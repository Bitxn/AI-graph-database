"""
LLM classification layer.

The deterministic differ decides WHAT changed; this module asks the model what
each change MEANS for consumers: a BREAKING / RISKY / COMPATIBLE verdict, a
one-line consumer-impact narrative, and a migration note.

Robustness contract: every change already carries a deterministic default
verdict. If the model call fails, returns malformed JSON, or skips a change,
that change keeps its default verdict — the gate never silently opens because
a model had a bad day. The model can also never invent a change: only ids we
sent are accepted back.
"""

from __future__ import annotations

import json
import re

from oneport_apidiff.config import Config
from oneport_apidiff.exceptions import ApidiffError, AuthError, RateLimitError
from oneport_apidiff.llm import complete
from oneport_apidiff.result import Change, Verdict

SYSTEM_PROMPT = """\
You are the classification layer of oneport-apidiff, a breaking-change gate for
Python codebases. A deterministic AST differ has already established a list of
API surface changes as hard facts. Your ONLY job is to judge what each change
means for consumers of this code. You must not invent, merge, or drop changes.

For each change, output:
- verdict: BREAKING (existing correct caller code stops working), RISKY
  (callers may misbehave without erroring, e.g. changed defaults or types), or
  COMPATIBLE (no existing call site is affected).
- impact: one sentence, concrete and consumer-centred, e.g. "callers passing
  timeout positionally now break".
- migration: one short actionable note, e.g. "pass retries= instead of
  attempts=", or "" if none is needed.

Each change includes a default_verdict from the deterministic rules and the
list of internal caller sites found by a textual scan (an over-approximation).
Prefer the default verdict unless the specifics justify otherwise — for
example, a removed symbol with zero internal callers in an internal-only
package per the team guidelines may be COMPATIBLE.

If team guidelines are provided, they take precedence over the defaults
(e.g. "private API, only __all__ symbols matter" means changes to symbols
outside __all__ are COMPATIBLE).

Respond with ONLY a JSON array, no prose, no markdown fences:
[{"id": 1, "verdict": "BREAKING", "impact": "...", "migration": "..."}, ...]
"""


def build_prompt(changes: list[Change], guidelines: str = "") -> str:
    payload = [
        {
            "id": c.id,
            "kind": c.kind.value,
            "file": c.file,
            "symbol": c.symbol,
            "detail": c.detail,
            "old_signature": c.old_signature,
            "new_signature": c.new_signature,
            "default_verdict": c.verdict.value,
            "internal_callers": [f"{r.file}:{r.line}  {r.snippet}" for r in c.callers[:10]],
        }
        for c in changes
    ]
    parts = []
    if guidelines:
        parts.append(f"TEAM GUIDELINES:\n{guidelines}\n")
    parts.append(f"CHANGES:\n{json.dumps(payload, indent=2)}")
    return "\n".join(parts)


def parse_response(text: str, changes: list[Change]) -> int:
    """
    Apply the model's classifications onto `changes` in place.

    Returns the number of changes actually classified by the model; the rest
    keep their deterministic default verdicts. Never raises on bad JSON.
    """
    cleaned = text.strip()
    # Strip a ```json ... ``` fence if the model added one despite instructions.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    # Fall back to the first [...] block in the text.
    if not cleaned.startswith("["):
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if not match:
            return 0
        cleaned = match.group(0)

    try:
        items = json.loads(cleaned)
    except json.JSONDecodeError:
        return 0
    if not isinstance(items, list):
        return 0

    by_id = {c.id: c for c in changes}
    applied = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        change = by_id.get(item.get("id"))
        if change is None:
            continue  # model hallucinated an id — ignore
        verdict_raw = str(item.get("verdict", "")).strip().upper()
        try:
            change.verdict = Verdict(verdict_raw)
        except ValueError:
            pass  # bad verdict string — keep the deterministic default
        change.impact = str(item.get("impact", "") or "")[:500]
        change.migration = str(item.get("migration", "") or "")[:500]
        applied += 1
    return applied


def classify(changes: list[Change], config: Config, guidelines: str = "") -> tuple[int, int]:
    """
    Classify `changes` in place via the configured model.

    Returns (classified_count, total_tokens). Auth/rate-limit errors propagate
    (the user should know their key is bad); anything else degrades gracefully
    to the deterministic default verdicts.
    """
    if not changes:
        return 0, 0

    prompt = build_prompt(changes, guidelines=guidelines)
    try:
        text, tokens = complete(
            model=config.model,
            api_key=config.api_key,
            system=SYSTEM_PROMPT,
            user=prompt,
            max_tokens=config.max_tokens,
        )
    except (AuthError, RateLimitError):
        raise
    except ApidiffError:
        return 0, 0  # deterministic defaults stand

    return parse_response(text, changes), tokens

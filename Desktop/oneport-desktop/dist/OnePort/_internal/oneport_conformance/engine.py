"""
Conformance engine — ties intent + diff + model into one honest verdict.

Flow: load intent → get the diff → ask the model whether the diff breaches any
intent rule → parse + ground the answer → apply waivers. Every step fails loud,
never fake-green: a missing intent doc, an empty diff, or an unreadable model
reply is reported as skip/error, not silently passed.
"""

from __future__ import annotations

import json

from oneport_conformance import diff_util, prompt
from oneport_conformance.intent import Intent, load_intent
from oneport_conformance.llm import judge
from oneport_conformance.result import SEVERITY_ORDER, ConformanceResult, Deviation
from oneport_conformance.waivers import apply_waivers, load_waivers

DEFAULT_MODEL = "gemini-flash-latest"


def check(intent_path: str, repo: str | None = None, mode: str = "head",
          base: str = "main", model: str = DEFAULT_MODEL,
          waiver_path: str | None = None,
          gemini_key: str | None = None) -> ConformanceResult:
    intent = load_intent(intent_path)

    diff = diff_util.get_diff(repo=repo, mode=mode, base=base)
    if not diff:
        return ConformanceResult(
            rules_checked=len(intent.rules), model=model,
            skipped=True, reason="No changes to check (empty diff).",
        )

    raw, tokens = judge(prompt.SYSTEM, prompt.build_user(intent, diff),
                        model=model, gemini_key=gemini_key)
    result = _parse(raw, intent)
    result.model = model
    result.tokens_used = tokens
    result.rules_checked = len(intent.rules)

    apply_waivers(result.deviations, load_waivers(waiver_path, repo))

    _add_honesty_notes(result)
    return result


def _parse(raw: str, intent: Intent) -> ConformanceResult:
    """Tolerant parse of the model's JSON envelope into grounded deviations."""
    data = _extract_json(raw)
    if data is None:
        return ConformanceResult(
            notes=["Could not parse the model's response as JSON — reporting as "
                   "inconclusive rather than a pass."],
            overall_confidence=0.0,
        )

    valid_ids = {r.id for r in intent.rules}
    rule_text = {r.id: r.text for r in intent.rules}
    deviations: list[Deviation] = []
    for d in data.get("deviations", []) or []:
        if not isinstance(d, dict):
            continue
        rid = str(d.get("rule_id", "")).strip()
        # Ground it: drop any deviation that doesn't cite a real intent rule.
        if rid not in valid_ids:
            continue
        sev = str(d.get("severity", "medium")).lower()
        if sev not in SEVERITY_ORDER:
            sev = "medium"
        deviations.append(Deviation(
            rule_id=rid,
            rule_text=rule_text[rid],
            observed=str(d.get("observed", "")).strip(),
            severity=sev,
            confidence=_clamp(d.get("confidence", 0.0)),
            explanation=str(d.get("explanation", "")).strip(),
            file=str(d.get("file", "")).strip(),
            line=_int(d.get("line", 0)),
        ))

    notes = [str(n).strip() for n in (data.get("notes") or []) if str(n).strip()]
    return ConformanceResult(
        deviations=deviations,
        overall_confidence=_clamp(data.get("overall_confidence", 0.0)),
        notes=notes,
    )


def _add_honesty_notes(result: ConformanceResult) -> None:
    active = result.active()
    if active and result.overall_confidence and result.overall_confidence < 0.5:
        result.notes.insert(0, (
            f"Low overall confidence ({result.overall_confidence:.0%}) — treat "
            "these as prompts for a human, not a hard verdict."
        ))
    low_conf = [d for d in active if d.confidence and d.confidence < 0.4]
    if low_conf:
        result.notes.append(
            f"{len(low_conf)} deviation(s) are low-confidence guesses — flagged "
            "for review, not asserted."
        )


def _extract_json(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    # Strip a ```json fence if present, else slice from first { to last }.
    if "```" in raw:
        seg = raw.split("```", 2)
        raw = seg[1] if len(seg) > 1 else raw
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _clamp(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def _int(v) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0

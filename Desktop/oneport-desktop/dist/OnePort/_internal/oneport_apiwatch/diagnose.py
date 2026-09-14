"""AI diagnosis layer (optional, Gemini).

On a failed check, feed the deterministic facts — what failed, the response
snippet, and the recent status/latency history — to Gemini and get back a
plain-English diagnosis, a ranked list of likely causes, and a suggested first
action.

Hard rule: a diagnosis failure (no key, rate limit, bad JSON) must NEVER change
the pass/fail verdict or the exit code. Errors are captured onto the Diagnosis
object and rendered as "unavailable", nothing more.
"""

from __future__ import annotations

import json

from oneport_apiwatch.config import Config
from oneport_apiwatch.exceptions import ApiwatchError
from oneport_apiwatch.history import History
from oneport_apiwatch.llm import call_gemini
from oneport_apiwatch.result import CheckResult, Diagnosis, Report

_SYSTEM = (
    "You are an SRE assistant embedded in an API health monitor. Given a failed "
    "endpoint check — the failure reasons, the HTTP response, and recent run "
    "history — produce a concise root-cause diagnosis. Weigh trends: latency "
    "climbing across recent runs, a status that just flipped, or repeated 5xx "
    "all point at different causes. Respond with ONLY a JSON object, no prose, "
    "no markdown fences, in this exact shape:\n"
    '{"summary": "one or two sentences", '
    '"likely_causes": ["most likely first", "..."], '
    '"suggested_action": "the single first thing to check or do"}'
)


def _build_prompt(check: CheckResult, recent: list[dict]) -> str:
    lines = [
        f"Check name: {check.name}",
        f"Request: {check.method} {check.url}",
        f"Expected status: {check.expected_statuses}; latency budget: "
        f"{check.latency_budget_ms}ms",
        f"Observed status: {check.status_code}; latency: {check.latency_ms}ms",
        f"Transport error: {check.error or 'none'}",
        "Failure reasons:",
    ]
    lines += [f"  - {reason}" for reason in check.failures]

    if check.assertions:
        lines.append("Body assertions:")
        for a in check.assertions:
            mark = "ok" if a.ok else "FAILED"
            lines.append(f"  - [{mark}] {a.path} expected {a.expected}, actual {a.actual}")

    if recent:
        lines.append("Recent runs (oldest first):")
        for run in recent:
            state = "up" if run.get("ok") else "down"
            lines.append(
                f"  - {run.get('timestamp', '?')}: {state} "
                f"status={run.get('status_code')} latency={run.get('latency_ms')}ms"
            )
    else:
        lines.append("Recent runs: none recorded yet (first run).")

    body = (check.body_snippet or "").strip()
    if body:
        lines.append("Response body (truncated):")
        lines.append(body[:1500])

    return "\n".join(lines)


def _parse_diagnosis(raw: str, model: str) -> Diagnosis:
    """Parse the model's JSON, tolerating stray markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` fences some models add despite instructions.
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip("`").strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fall back to using the raw text as the summary — still useful to a human.
        return Diagnosis(summary=raw.strip()[:500], model=model)

    causes = data.get("likely_causes") or []
    if not isinstance(causes, list):
        causes = [str(causes)]
    return Diagnosis(
        summary=str(data.get("summary", "")).strip(),
        likely_causes=[str(c) for c in causes],
        suggested_action=str(data.get("suggested_action", "")).strip(),
        model=model,
    )


def diagnose_check(check: CheckResult, config: Config, recent: list[dict]) -> Diagnosis:
    """Diagnose one failed check. Never raises — errors land on the Diagnosis."""
    from oneport_account import is_logged_in
    if not is_logged_in():
        return Diagnosis(
            summary="",
            model=config.model,
            error="not logged in to Oneport (diagnosis skipped) — "
                  "run: oneport-account login <op_live_...>",
        )
    prompt = _build_prompt(check, recent)
    try:
        raw, _tokens = call_gemini(
            model=config.model,
            api_key=config.api_key,
            system=_SYSTEM,
            user=prompt,
            max_tokens=config.max_tokens,
        )
    except ApiwatchError as exc:
        return Diagnosis(summary="", model=config.model, error=str(exc))
    return _parse_diagnosis(raw, config.model)


def diagnose_report(report: Report, config: Config, history: History | None = None) -> None:
    """Attach an AI diagnosis to every failed check in the report (in place)."""
    for check in report.failed:
        recent = history.recent(check.name) if history else []
        check.diagnosis = diagnose_check(check, config, recent)

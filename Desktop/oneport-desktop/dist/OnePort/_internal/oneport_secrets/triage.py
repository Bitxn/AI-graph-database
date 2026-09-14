"""
The wedge: LLM triage.

Deterministic detection has already found candidate secrets. The model's ONLY
job here is to classify each one REAL vs FALSE-POSITIVE (test fixture, example
placeholder, documentation sample) with a one-line reason — so we deliver the
signal trufflehog buries in noise.

Hard rules:
  * The model never adds, removes, or relocates a finding. It annotates by index.
  * Any finding the model doesn't return, or returns malformed, stays UNREVIEWED
    — which still blocks the gate (fail-safe: better a false alarm than a leak).
  * If there's no API key, we skip triage entirely and everything blocks.
"""

from __future__ import annotations

import json
import os

from oneport_secrets.config import Config
from oneport_secrets.exceptions import AuthError
from oneport_secrets.llm import call_gemini, is_gemini_model
from oneport_secrets.result import _SEV_ORDER, Finding, ScanResult, Verdict

# Hard cap on how many candidate findings go to the model in one triage call.
# A whole-tree scan of a large repo can surface hundreds of entropy hits; sending
# them all in one request is what burns tens of thousands of tokens. 60 comfortably
# covers any real change set; override with ONEPORT_SECRETS_MAX_TRIAGE.
_DEFAULT_TRIAGE_CAP = 60


def _triage_cap() -> int:
    try:
        return max(1, int(os.getenv("ONEPORT_SECRETS_MAX_TRIAGE", str(_DEFAULT_TRIAGE_CAP))))
    except ValueError:
        return _DEFAULT_TRIAGE_CAP


TRIAGE_SYSTEM_PROMPT = ""
# NOTE: the tuned system prompt for this call lives SERVER-SIDE in the
# Oneport prompt vault, keyed by tool:action — the proxy injects it at
# call time and ignores this placeholder. The prompt is intentionally
# not shipped in this package.



def _build_user_prompt(findings: list[Finding]) -> str:
    lines = ["Findings to triage:\n"]
    for i, f in enumerate(findings):
        loc = f"{f.path}:{f.line}" + (f" (git history commit {f.commit[:8]})" if f.commit else "")
        lines.append(
            f"[{i}] detector={f.detector_name} ({f.detector_id})\n"
            f"    location: {loc}\n"
            f"    value (masked): {f.redacted()}\n"
            f"    line: {f.masked_line()}\n"
        )
    return "\n".join(lines)


class Triager:
    """Applies LLM triage verdicts onto a ScanResult in place."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def triage(self, result: ScanResult) -> ScanResult:
        """Classify every finding. No-op (leaves everything UNREVIEWED) when
        there are no findings or no API key. Sets result.triaged only if the
        model actually ran and returned verdicts.
        """
        if not result.findings:
            result.triaged = True
            return result
        if not self.config.has_key:   # has_key == logged in to Oneport
            # No brain available — fail safe, everything stays blocking.
            return result

        # Cost guardrail: never send an unbounded pile of candidates to the model
        # in one call (a whole-tree scan of a big repo can surface hundreds, which
        # would burn tens of thousands of tokens in a single request). Triage the
        # highest-severity findings up to a hard cap; anything beyond the cap stays
        # UNREVIEWED, which still BLOCKS (fail-safe) and is reported as such.
        all_findings = result.findings
        cap = _triage_cap()
        if len(all_findings) > cap:
            findings = sorted(all_findings, key=lambda f: _SEV_ORDER.get(f.severity, 9))[:cap]
            result.triage_capped = len(all_findings) - cap
        else:
            findings = all_findings

        raw = self._call_model(TRIAGE_SYSTEM_PROMPT, _build_user_prompt(findings))
        verdicts = _parse_verdicts(raw)

        for item in verdicts:
            idx = item.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(findings)):
                continue
            verdict = str(item.get("verdict", "")).lower()
            if verdict == "false_positive":
                findings[idx].verdict = Verdict.FALSE_POSITIVE
            elif verdict == "real":
                findings[idx].verdict = Verdict.REAL
            else:
                continue
            findings[idx].reason = str(item.get("reason", "")).strip()

        result.model = self.config.model
        result.total_tokens = getattr(self, "_last_tokens", 0)
        result.triaged = True
        return result

    def _call_model(self, system: str, user: str) -> str:
        if is_gemini_model(self.config.model):
            text, tokens = call_gemini(
                model=self.config.model,
                api_key=self.config.api_key,
                system=system,
                user=user,
                max_tokens=self.config.max_tokens,
            )
            self._last_tokens = tokens
            return text
        # Only Gemini is wired in this tool; guard clearly rather than fail deep.
        raise AuthError(
            f"Model '{self.config.model}' is not supported here. Set GEMINI_API_KEY "
            "and use a gemini-* model."
        )


def _parse_verdicts(raw: str) -> list[dict]:
    """Pull triage verdicts out of the model response, tolerating shape drift.

    Models are inconsistent about the envelope: fenced, wrapped in a preamble,
    returned as a bare array, or keyed differently ("triage_decision" instead of
    "verdicts"). None of that is a reason to fail the gate — this NEVER raises.
    Anything we can't read yields no verdicts, which leaves findings UNREVIEWED,
    and UNREVIEWED still blocks (fail-safe: better a false alarm than a leak).
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip()

    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Salvage the outermost JSON object/array out of surrounding prose.
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = text.find(opener), text.rfind(closer)
            if start != -1 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                    break
                except json.JSONDecodeError:
                    continue
    if data is None:
        return []

    if isinstance(data, list):
        return [v for v in data if isinstance(v, dict)]
    if isinstance(data, dict):
        for key in ("verdicts", "triage_decision", "triage", "decisions",
                    "results", "findings", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [v for v in value if isinstance(v, dict)]
        # A single verdict returned bare, unwrapped.
        if "index" in data or "verdict" in data:
            return [data]
    return []

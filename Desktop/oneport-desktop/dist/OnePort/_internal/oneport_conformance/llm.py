"""
Model access via the OnePort managed proxy.

Only the prompt (enumerated intent + diff) is sent through the OnePort backend,
which runs the model with a server-side key and meters tokens against the
logged-in account. The diff and intent assembly stay local.
"""

from __future__ import annotations

import json
import os

from oneport_account import (
    AccountError,
    NotLoggedIn,
    OutOfTokens,
    RateLimited,
    call_managed_llm,
)

from oneport_conformance.exceptions import AuthError, OneportError, RateLimitError

_TEMPERATURE = 0.1  # same intent + same diff → same verdict

# For a bring-your-own-key run, the managed alias maps to a concrete public id.
_BYOK_MODEL_ALIAS = {"gemini-flash-latest": "gemini-2.0-flash"}


def judge(system: str, user: str, model: str, max_tokens: int = 2000,
          timeout: int = 180, gemini_key: str | None = None) -> tuple[str, int]:
    """One completion. Returns (text, total_tokens).

    Routes through the OnePort managed proxy by default (metered against the
    logged-in account). If a Gemini key is supplied (arg or GEMINI_API_KEY), it
    calls Gemini directly instead — no OnePort tokens spent. JSON mode is forced.
    """
    key = gemini_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return _judge_byok(system, user, model, max_tokens, timeout, key)

    try:
        result = call_managed_llm(
            system=system,
            user=user,
            tool="conformance",
            action="check",
            model=model,
            max_tokens=max_tokens,
            temperature=_TEMPERATURE,
            response_json=True,
            thinking_budget=0,
            timeout=timeout,
        )
    except NotLoggedIn as exc:
        raise AuthError(
            "Not logged in to OnePort. Run `oneport-account login <token>`."
        ) from exc
    except OutOfTokens as exc:
        raise AuthError(f"Out of OnePort tokens. Top up at {exc.buy_url}") from exc
    except RateLimited as exc:
        raise RateLimitError(str(exc)) from exc
    except AccountError as exc:
        raise OneportError(f"OnePort service error: {exc}") from exc

    return result.text, result.tokens_used


def _judge_byok(system: str, user: str, model: str, max_tokens: int,
                timeout: int, key: str) -> tuple[str, int]:
    """Call Gemini directly with the user's own API key (no OnePort tokens)."""
    import httpx  # already present via oneport-account

    model_id = _BYOK_MODEL_ALIAS.get(model, model)
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model_id}:generateContent")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": _TEMPERATURE,
            "responseMimeType": "application/json",
            "maxOutputTokens": max_tokens,
        },
    }
    try:
        resp = httpx.post(url, params={"key": key}, json=body, timeout=timeout)
    except httpx.HTTPError as exc:
        raise OneportError(f"Gemini request failed: {exc}") from exc

    if resp.status_code in (401, 403):
        raise AuthError("Gemini rejected the API key (GEMINI_API_KEY).")
    if resp.status_code == 429:
        raise RateLimitError("Gemini rate-limited the key — try again shortly.")
    if resp.status_code >= 400:
        raise OneportError(f"Gemini error {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        tokens = int(data.get("usageMetadata", {}).get("totalTokenCount", 0))
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise OneportError(f"Unexpected Gemini response shape: {exc}") from exc
    return text, tokens

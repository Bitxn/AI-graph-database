"""
The account client — the single door every Oneport tool uses to run a metered
LLM call. Tools import `call_managed_llm` and never touch a model key: the token
usage is billed against the logged-in user's balance by the backend proxy.

  from oneport_account import call_managed_llm, OutOfTokens, NotLoggedIn
  text, tokens, balance = call_managed_llm(system, user, tool="apidiff")
"""

from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass

import httpx

from oneport_account.config import DEFAULT_BUY_URL, api_base
from oneport_account.credentials import Credentials, load
from oneport_account.exceptions import (
    AccountError, NotLoggedIn, OutOfTokens, RateLimited,
)

DEFAULT_MODEL = "gemini-flash-latest"
_TIMEOUT = 180

# ── client-side retry policy ──────────────────────────────────────────────────
# This function is the single choke point every Oneport tool calls, so retry
# lives HERE — one implementation, all ~15 tools covered. Policy:
#   * 503 upstream_overloaded → retry up to 3 times (2s/4s + jitter): the model
#     being momentarily busy is exactly what a human's "run it again" fixes.
#   * 429 upstream_rate_limited → ONE retry after ~4s: a shared-key burst can
#     clear in seconds, but hammering a rate-limited key makes it worse.
#   * 429 tier_rate_limited / daily_cap_reached → NO retry: an rpm/daily cap
#     will not clear inside a CLI call; surface it immediately with the fix
#     (wait / upgrade) instead of stalling the gate for a minute.
#   * network errors → one retry: transient blips shouldn't fail a CI gate.
# Metering happens server-side only on success, so retries never double-charge.
_OVERLOAD_ATTEMPTS = 3
_NO_RETRY_429 = ("tier_rate_limited", "daily_cap_reached", "quota", "billing")


def _backoff_s(attempt: int, base: float = 2.0) -> float:
    return base * (2 ** attempt) + random.uniform(0, 0.5)


@dataclass
class LLMResult:
    text: str
    tokens_used: int
    balance: int


def _creds_or_raise() -> Credentials:
    creds = load()
    if not creds:
        raise NotLoggedIn()
    return creds


def call_managed_llm(
    system: str,
    user: str,
    tool: str,
    action: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2048,
    temperature: float = 0.1,
    repo_hash: str = "",
    response_json: bool = False,
    thinking_budget: int | None = None,
    timeout: int = _TIMEOUT,
) -> LLMResult:
    """Run one metered completion via the Oneport proxy. Raises NotLoggedIn /
    OutOfTokens / RateLimited / AccountError so the caller can prompt correctly.

    response_json=True forces a raw-JSON reply (for extraction tools); pass
    thinking_budget=0 to disable a thinking model's reasoning tokens on
    structured-output calls."""
    creds = _creds_or_raise()
    url = api_base(creds.api_url) + "/llm"
    payload = {
        "system": system, "user": user, "model": model, "max_tokens": max_tokens,
        "temperature": temperature, "tool": tool, "action": action, "repo_hash": repo_hash,
    }
    if response_json:
        payload["response_json"] = True
    if thinking_budget is not None:
        payload["thinking_budget"] = thinking_budget

    # One idempotency key per logical call, REUSED across every retry below. If a
    # response is lost after the server already charged, the retry carries the
    # same key and the backend replays the original result instead of charging
    # again. Generated once here, outside the loop, on purpose.
    idempotency_key = uuid.uuid4().hex
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Idempotency-Key": idempotency_key,
    }
    resp: httpx.Response | None = None
    attempt = 0
    retried_429 = False
    retried_net = False
    while True:
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        except httpx.HTTPError as exc:
            if not retried_net:
                retried_net = True
                time.sleep(_backoff_s(0, base=1.0))
                continue
            raise AccountError(f"Could not reach the Oneport service: {exc}") from exc

        if resp.status_code == 503 and attempt < _OVERLOAD_ATTEMPTS:
            # Model momentarily overloaded — the definition of transient.
            time.sleep(_backoff_s(attempt))
            attempt += 1
            continue
        if resp.status_code == 429 and not retried_429:
            # Retry ONCE unless the server says it's a cap that won't clear
            # (tier rpm / daily / quota) — those need the user, not a retry.
            kind = (_field(resp, "error", "") + " " + _field(resp, "detail", "")).lower()
            if not any(marker in kind for marker in _NO_RETRY_429):
                retried_429 = True
                time.sleep(_backoff_s(1))     # ~4s + jitter
                continue
        break

    if resp.status_code == 401:
        raise NotLoggedIn("Your token is invalid or was revoked. Log in again.")
    if resp.status_code == 402:
        raise OutOfTokens(_field(resp, "buy_url", DEFAULT_BUY_URL))
    if resp.status_code in (429, 503):
        # Retries exhausted (or a non-retryable cap). Surface the server's
        # reason so the user knows whether to wait a minute or upgrade their
        # tier — humanized, since the proxy forwards the provider's raw JSON
        # error body verbatim.
        raise RateLimited(humanize_upstream_error(_field(resp, "detail", "")))
    if resp.status_code >= 400:
        raise AccountError(f"Service error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    return LLMResult(text=data.get("text", ""),
                    tokens_used=int(data.get("tokens_used", 0)),
                    balance=int(data.get("balance", 0)))


def fetch_balance() -> dict:
    """Account balance + usage summary. Raises NotLoggedIn / AccountError."""
    creds = _creds_or_raise()
    try:
        resp = httpx.get(api_base(creds.api_url) + "/account",
                        headers={"Authorization": f"Bearer {creds.token}"}, timeout=30)
    except httpx.HTTPError as exc:
        raise AccountError(f"Could not reach the Oneport service: {exc}") from exc
    if resp.status_code == 401:
        raise NotLoggedIn("Your token is invalid or was revoked.")
    if resp.status_code >= 400:
        raise AccountError(f"Service error {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def redeem_code(code: str) -> dict:
    """Redeem a free-token code. Returns {granted, balance}. Raises AccountError."""
    creds = _creds_or_raise()
    try:
        resp = httpx.post(api_base(creds.api_url) + "/redeem",
                         headers={"Authorization": f"Bearer {creds.token}"},
                         json={"code": code}, timeout=30)
    except httpx.HTTPError as exc:
        raise AccountError(f"Could not reach the Oneport service: {exc}") from exc
    if resp.status_code == 401:
        raise NotLoggedIn()
    if resp.status_code >= 400:
        detail = _field(resp, "detail", resp.text[:200])
        raise AccountError(detail)
    return resp.json()


def validate_token(token: str, api_url: str = "") -> dict:
    """Check a token against /account before saving it. Returns the account info."""
    base = api_base(api_url)
    try:
        resp = httpx.get(base + "/account",
                        headers={"Authorization": f"Bearer {token}"}, timeout=30)
    except httpx.HTTPError as exc:
        raise AccountError(f"Could not reach the Oneport service: {exc}") from exc
    if resp.status_code == 401:
        raise AccountError("That token is not valid.")
    if resp.status_code >= 400:
        raise AccountError(f"Service error {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def is_logged_in() -> bool:
    return load() is not None


def _field(resp: httpx.Response, key: str, default: str) -> str:
    # Always returns a str (its declared contract). The proxy sometimes sends a
    # structured `detail` (dict/list) on 4xx/429; callers concatenate + lower()
    # this, so a non-str here used to crash with a confusing TypeError instead of
    # surfacing the real rate-limit / error. Coerce defensively.
    try:
        val = resp.json().get(key, default)
    except (ValueError, AttributeError):
        return default
    if val is None:
        return default
    return val if isinstance(val, str) else str(val)


_DEFAULT_RATE_MSG = "The model is rate-limited right now — try again shortly."


def humanize_upstream_error(raw: str, default: str = _DEFAULT_RATE_MSG) -> str:
    """Turn a provider error body into one sentence a human can act on.

    The proxy passes the provider's error straight through in `detail`, so `detail`
    can be a whole Gemini error object:

        {"error": {"code": 429, "message": "You exceeded your current quota ...
         limit: 20, model: gemini-3.5-flash", "status": "RESOURCE_EXHAUSTED", ...}}

    Dumping that at someone running `op apidiff check` is not an error message.
    Sanitizing HERE rather than in each tool is deliberate: this is the single
    choke point every one of the tools calls, so one fix covers all of them — and
    tools can go back to surfacing the real reason instead of hardcoding a
    friendly lie ("try again shortly" is wrong advice when a DAILY cap is hit).
    """
    text = (raw or "").strip()
    if not text:
        return default

    # Unwrap a JSON error envelope if that's what we were handed.
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except ValueError:
            return default
        while isinstance(data, dict) and "error" in data:
            data = data["error"]
        if isinstance(data, dict):
            text = str(data.get("message") or data.get("detail") or "").strip()
        elif isinstance(data, str):
            text = data.strip()
        else:
            return default
        if not text:
            return default

    # Quota exhaustion is not "try again shortly" — waiting a moment won't help if
    # it's a daily cap. Name it so the user knows to check their tier.
    low = text.lower()
    if "quota" in low or "resource_exhausted" in low or "billing" in low:
        return ("Model quota exceeded for your API tier — check your plan/billing "
                "limits, or wait for the quota window to reset.")

    # Otherwise: first sentence only, bounded. Provider messages append doc links
    # and retry hints that read as noise in a CLI.
    first = text.split("\n")[0].split(". ")[0].strip().rstrip(".")
    if not first:
        return default
    return (first[:160] + "…") if len(first) > 160 else first + "."

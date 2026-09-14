"""
Model access — now the Oneport managed proxy.

Depcheck holds no API key. All detection (manifest parsing, OSV.dev lookups,
license + CVSS checks) stays 100% local and deterministic; only the CVE
exploitability *triage* judgment is sent — the already-built prompt, never your
code — through the Oneport backend, which meters the tokens against the
logged-in account.

`call_llm` keeps its old signature so `triage.py` is unchanged; the `api_key`
argument is accepted for compatibility and ignored.
"""

from __future__ import annotations

from oneport_account import (
    AccountError,
    NotLoggedIn,
    OutOfTokens,
    RateLimited,
    call_managed_llm,
)

from oneport_depcheck.exceptions import AuthError, DepcheckError, RateLimitError

# Triage returns a JSON array; ask the proxy for raw JSON and disable thinking
# tokens (they'd otherwise risk truncating the array on a thinking model).
_RESPONSE_JSON = True
_THINKING_BUDGET = 0
_TEMPERATURE = 0.2  # deterministic-ish: same evidence → same classification


def is_gemini_model(model: str) -> bool:
    return model.lower().startswith("gemini")


def call_llm(
    model: str,
    api_key: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: int = 180,
) -> tuple[str, int]:
    """One metered completion via the Oneport proxy. Returns (text, total_tokens).

    Raises AuthError / RateLimitError / DepcheckError so the CLI handles it the
    same as before. `api_key` is ignored (the proxy holds the key)."""
    try:
        result = call_managed_llm(
            system=system,
            user=user,
            tool="depcheck",
            action="triage",
            model=model,
            max_tokens=max_tokens,
            temperature=_TEMPERATURE,
            response_json=_RESPONSE_JSON,
            thinking_budget=_THINKING_BUDGET,
            timeout=timeout,
        )
    except NotLoggedIn as exc:
        raise AuthError(
            "Not logged in to Oneport. Run `oneport-account login <token>` "
            "(get a free token at https://oneport.dev), or use --no-llm for "
            "detection-only output."
        ) from exc
    except OutOfTokens as exc:
        raise AuthError(f"Out of Oneport tokens. Top up at {exc.buy_url}") from exc
    except RateLimited as exc:
        raise RateLimitError(
            "The model is rate-limited right now — try again shortly."
        ) from exc
    except AccountError as exc:
        raise DepcheckError(f"Oneport service error: {exc}") from exc

    return result.text, result.tokens_used


# Back-compat aliases: some paths / tests referenced these directly.
def call_gemini(model, api_key, system, user, max_tokens, timeout: int = 180):
    return call_llm(model, api_key, system, user, max_tokens, timeout)

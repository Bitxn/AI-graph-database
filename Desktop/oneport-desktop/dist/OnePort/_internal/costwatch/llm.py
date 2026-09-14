"""
Model access — now the Oneport managed proxy.

Costwatch no longer talks to Gemini/Claude directly and holds no API key: the
deterministic IaC parsing + pricing stays 100% local, and only the final
waste-analysis judgment is sent (already-built prompt, never your files) through
the Oneport backend, which meters the token usage against the logged-in account.

`call_model` keeps its old signature so `analyzer.py` is unchanged; the `api_key`
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

from costwatch.exceptions import AuthError, CostwatchError, RateLimitError

# Costwatch does structured JSON extraction, so ask the proxy for a raw-JSON
# reply and disable thinking tokens (they'd otherwise starve the answer on a
# thinking model). These map to Gemini's responseMimeType / thinkingConfig.
_RESPONSE_JSON = True
_THINKING_BUDGET = 0
_TEMPERATURE = 0.2  # deterministic-ish: same IaC → same findings


def is_gemini_model(model: str) -> bool:
    return model.lower().startswith("gemini")


def call_model(
    model: str,
    api_key: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: int = 180,
) -> tuple[str, int]:
    """One metered completion via the Oneport proxy. Returns (text, total_tokens).

    Raises AuthError / RateLimitError / CostwatchError so the CLI handles it the
    same as before. `api_key` is ignored (the proxy holds the key)."""
    try:
        result = call_managed_llm(
            system=system,
            user=user,
            tool="costwatch",
            action="analyze",
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
            "(get a free token at https://oneport.dev)."
        ) from exc
    except OutOfTokens as exc:
        raise AuthError(
            f"Out of Oneport tokens. Top up at {exc.buy_url}"
        ) from exc
    except RateLimited as exc:
        raise RateLimitError(
            "The model is rate-limited right now — try again shortly."
        ) from exc
    except AccountError as exc:
        raise CostwatchError(f"Oneport service error: {exc}") from exc

    return result.text, result.tokens_used


# Back-compat alias: some call paths / tests referenced call_gemini directly.
def call_gemini(
    model: str,
    api_key: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: int = 180,
) -> tuple[str, int]:
    return call_model(model, api_key, system, user, max_tokens, timeout)

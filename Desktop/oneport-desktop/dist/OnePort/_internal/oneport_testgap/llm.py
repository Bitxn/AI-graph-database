"""
Model access — now the Oneport managed proxy.

Coverage analysis + gap detection stay 100% local; only the ranking and
test-generation prompts are sent — already built, never your whole repo —
through the Oneport backend, which runs the model with a server-side key and
meters the tokens against the logged-in account.

`complete` keeps its old signature so `ranker.py`/`generator.py` are unchanged;
the `api_key` argument is accepted for compatibility and ignored. (Test-code
generation and JSON ranking share this path, so we do NOT force JSON mode — the
ranker already tolerates fenced/extracted JSON.)
"""

from __future__ import annotations

from oneport_account import (
    AccountError,
    NotLoggedIn,
    OutOfTokens,
    RateLimited,
    call_managed_llm,
)

from oneport_testgap.exceptions import AuthError, OneportError, RateLimitError

_TEMPERATURE = 0.2  # same gaps → same ranking


def is_gemini_model(model: str) -> bool:
    return model.lower().startswith("gemini")


def complete(
    model: str,
    api_key: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: int = 180,
) -> tuple[str, int]:
    """One metered completion via the Oneport proxy. Returns (text, total_tokens).

    Raises AuthError / RateLimitError / OneportError. `api_key` is ignored."""
    try:
        result = call_managed_llm(
            system=system,
            user=user,
            tool="testgap",
            action="analyze",
            model=model,
            max_tokens=max_tokens,
            temperature=_TEMPERATURE,
            timeout=timeout,
        )
    except NotLoggedIn as exc:
        raise AuthError(
            "Not logged in to Oneport. Run `oneport-account login <token>` "
            "(free token at https://oneport.dev)."
        ) from exc
    except OutOfTokens as exc:
        raise AuthError(f"Out of Oneport tokens. Top up at {exc.buy_url}") from exc
    except RateLimited as exc:
        raise RateLimitError("The model is rate-limited right now — try again shortly.") from exc
    except AccountError as exc:
        raise OneportError(f"Oneport service error: {exc}") from exc

    return result.text, result.tokens_used


# Back-compat alias: some paths referenced call_gemini directly.
def call_gemini(model, api_key, system, user, max_tokens, timeout: int = 180):
    return complete(model, api_key, system, user, max_tokens, timeout)

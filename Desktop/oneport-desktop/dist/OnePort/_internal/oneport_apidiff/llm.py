"""
Model access for oneport-apidiff — routed through the Oneport managed proxy.

No BYOK: the tool never holds a model key. `complete()` sends the prompt to the
Oneport backend, which meters the logged-in user's token balance and calls the
model with the server-side key. Account/billing errors are mapped onto apidiff's
own exception taxonomy so the CLI handles them the same way as before.
"""

from __future__ import annotations

from oneport_account import (
    AccountError,
    NotLoggedIn,
    OutOfTokens,
    RateLimited,
    call_managed_llm,
)

from oneport_apidiff.exceptions import ApidiffError, AuthError, RateLimitError

TOOL = "apidiff"


def is_gemini_model(model: str) -> bool:
    # kept for callers/tests that still probe the model name
    return model.lower().startswith("gemini")


def complete(
    model: str,
    api_key: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: int = 180,
    action: str = "classify",
) -> tuple[str, int]:
    """One metered completion via the Oneport proxy. Returns (text, tokens_used).

    `api_key` is accepted but ignored — keys are managed server-side. Raises
    AuthError (not logged in / out of tokens), RateLimitError, or ApidiffError.
    """
    try:
        result = call_managed_llm(
            system=system, user=user, tool=TOOL, action=action,
            model=model, max_tokens=max_tokens, timeout=timeout,
        )
    except OutOfTokens as exc:
        raise AuthError(str(exc)) from exc
    except NotLoggedIn as exc:
        raise AuthError(str(exc)) from exc
    except RateLimited as exc:
        raise RateLimitError(str(exc)) from exc
    except AccountError as exc:
        raise ApidiffError(str(exc)) from exc
    return result.text, result.tokens_used

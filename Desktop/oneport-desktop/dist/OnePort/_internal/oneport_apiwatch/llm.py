"""
Model access for the optional AI diagnosis layer — routed through the Oneport
managed proxy.

No BYOK: the tool never holds a model key. `call_gemini()` (name kept for the
existing caller) sends the prompt to the Oneport backend, which meters the
logged-in user's token balance and calls the model with the server-side key.

The AI layer is strictly optional — the deterministic probe (probe.py) needs no
account and no network beyond the endpoints being checked. This module is only
touched under `--explain`. Account/billing errors are raised as ApiwatchError so
the diagnosis degrades gracefully and never changes the pass/fail verdict.
"""

from __future__ import annotations

from oneport_account import (
    AccountError,
    NotLoggedIn,
    OutOfTokens,
    RateLimited,
    call_managed_llm,
)

from oneport_apiwatch.exceptions import ApiwatchError, AuthError, RateLimitError

TOOL = "apiwatch"


def is_gemini_model(model: str) -> bool:
    return model.lower().startswith("gemini")


def call_gemini(
    model: str,
    api_key: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: int = 60,
) -> tuple[str, int]:
    """One metered completion via the Oneport proxy. Returns (text, tokens_used).

    `api_key` is accepted but ignored — keys are managed server-side. Raises
    AuthError (not logged in) / RateLimitError / ApiwatchError, all of which the
    caller catches so a failed diagnosis never changes the deterministic verdict.
    """
    try:
        result = call_managed_llm(
            system=system, user=user, tool=TOOL, action="diagnose",
            model=model, max_tokens=max_tokens, timeout=timeout,
        )
    except (NotLoggedIn, OutOfTokens) as exc:
        raise AuthError(str(exc)) from exc
    except RateLimited as exc:
        raise RateLimitError(str(exc)) from exc
    except AccountError as exc:
        raise ApiwatchError(str(exc)) from exc
    return result.text, result.tokens_used

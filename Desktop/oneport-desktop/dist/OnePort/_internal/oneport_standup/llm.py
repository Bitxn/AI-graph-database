"""Model access — now the Oneport managed proxy.

Standup reads your git history locally and only sends the already-built prompt
(commit subjects + stats, never your code) through the Oneport backend, which
runs the model with a server-side key and meters the tokens against the
logged-in account. `call_llm` keeps its old signature; `api_key` is ignored."""

from __future__ import annotations

from oneport_account import (
    AccountError,
    NotLoggedIn,
    OutOfTokens,
    RateLimited,
    call_managed_llm,
)

from oneport_standup.exceptions import AuthError, StandupError

# The narrator asks for a JSON envelope, so request raw JSON and disable thinking
# tokens (they'd otherwise risk truncating the reply on a thinking model).
_RESPONSE_JSON = True
_THINKING_BUDGET = 0
_TEMPERATURE = 0.2


def is_gemini_model(model: str) -> bool:
    return model.lower().startswith("gemini")


def call_llm(model: str, api_key: str, system: str, user: str,
             max_tokens: int, timeout: int = 120) -> tuple[str, int]:
    """One metered completion via the Oneport proxy. Returns (text, total_tokens).
    Raises AuthError / StandupError. `api_key` is ignored (the proxy holds it)."""
    try:
        result = call_managed_llm(
            system=system,
            user=user,
            tool="standup",
            action="narrate",
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
            "(free token at https://oneport.dev), or use --no-llm for the "
            "deterministic report."
        ) from exc
    except OutOfTokens as exc:
        raise AuthError(f"Out of Oneport tokens. Top up at {exc.buy_url}") from exc
    except RateLimited as exc:
        raise StandupError(
            "The model is rate-limited right now — try again shortly, or use "
            "--no-llm for the deterministic report."
        ) from exc
    except AccountError as exc:
        raise StandupError(f"Oneport service error: {exc}") from exc

    return result.text, result.tokens_used

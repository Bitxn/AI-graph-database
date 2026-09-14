"""
Model access — now the Oneport managed proxy.

The diff/context assembly stays local; only the review prompt is sent through
the Oneport backend, which runs the model with a server-side key and meters the
tokens against the logged-in account. With the default model a Gemini model,
`is_gemini_model()` is always true, so every caller routes through here.

`call_gemini` keeps its old signature so `reviewer.py`/`chat.py` are unchanged;
the `api_key` argument is accepted for compatibility and ignored. (Review
findings are JSON but PR chat is prose, so we do NOT force JSON mode — the
finding parser already tolerates fenced/extracted JSON.)
"""

from __future__ import annotations

from oneport_account import (
    AccountError,
    NotLoggedIn,
    OutOfTokens,
    RateLimited,
    call_managed_llm,
)

from oneport.exceptions import AuthError, OneportError, RateLimitError

_TEMPERATURE = 0.2  # same diff → same findings


def is_gemini_model(model: str) -> bool:
    return model.lower().startswith("gemini")


def call_gemini(
    model: str,
    api_key: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: int = 180,
    response_json: bool = False,
) -> tuple[str, int]:
    """One metered completion via the Oneport proxy. Returns (text, total_tokens).

    Pass response_json=True for the findings call (it expects a JSON issues
    envelope) — that forces raw JSON AND disables thinking tokens, which would
    otherwise eat the output budget and truncate the JSON on a thinking model.
    Chat/prose callers leave it False. Raises AuthError / RateLimitError /
    OneportError. `api_key` is ignored."""
    try:
        result = call_managed_llm(
            system=system,
            user=user,
            tool="review",
            action="review",
            model=model,
            max_tokens=max_tokens,
            temperature=_TEMPERATURE,
            response_json=response_json,
            thinking_budget=0 if response_json else None,
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
        # Surface the account layer's reason (already humanized there) — a
        # hardcoded "try again shortly" is wrong advice when the real cause is a
        # daily quota cap that no amount of waiting-a-moment will clear.
        raise RateLimitError(str(exc)) from exc
    except AccountError as exc:
        raise OneportError(f"Oneport service error: {exc}") from exc

    return result.text, result.tokens_used

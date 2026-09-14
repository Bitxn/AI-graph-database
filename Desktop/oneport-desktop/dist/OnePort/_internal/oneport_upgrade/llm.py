"""Model access — now the Oneport managed proxy.

Upgrade scans and applies codemods 100% locally; only the `plan` step sends the
already-built findings summary (never your source) through the Oneport backend,
which runs the model with a server-side key and meters the tokens against the
logged-in account. `call_llm` keeps its old signature so `planner.py` is
unchanged; `api_key` is accepted for compatibility and ignored."""

from __future__ import annotations

from oneport_account import (
    AccountError,
    NotLoggedIn,
    OutOfTokens,
    RateLimited,
    call_managed_llm,
)

from oneport_upgrade.exceptions import AuthError, UpgradeError

# The planner expects a JSON object, so request raw JSON and disable thinking
# tokens (they'd otherwise risk truncating the reply on a thinking model).
_RESPONSE_JSON = True
_THINKING_BUDGET = 0
_TEMPERATURE = 0.1


def is_gemini_model(model: str) -> bool:
    # kept for callers/tests that still probe the model name
    return model.lower().startswith("gemini")


def call_llm(model: str, api_key: str, system: str, user: str,
             max_tokens: int, timeout: int = 120) -> tuple[str, int]:
    """One metered completion via the Oneport proxy. Returns (text, total_tokens).

    Raises AuthError / UpgradeError so the planner's existing `except UpgradeError`
    degrades gracefully (no plan, but scan/apply are unaffected). `api_key` is
    ignored — the proxy holds the key server-side."""
    try:
        result = call_managed_llm(
            system=system,
            user=user,
            tool="upgrade",
            action="plan",
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
            "(free token at https://oneport.dev). scan/apply need no account."
        ) from exc
    except OutOfTokens as exc:
        raise AuthError(f"Out of Oneport tokens. Top up at {exc.buy_url}") from exc
    except RateLimited as exc:
        # Surface the account layer's reason (already humanized there) — a
        # hardcoded "try again shortly" is wrong advice when the real cause is a
        # daily quota cap that no amount of waiting-a-moment will clear.
        raise UpgradeError(str(exc)) from exc
    except AccountError as exc:
        raise UpgradeError(f"Oneport service error: {exc}") from exc

    return result.text, result.tokens_used

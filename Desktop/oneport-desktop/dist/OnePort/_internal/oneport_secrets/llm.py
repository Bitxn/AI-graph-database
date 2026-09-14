"""
Model access — now the Oneport managed proxy.

Secret *detection* (regex, entropy, git history) is 100% local and never touches
a model. Only the exploitability *triage* judgment is sent — the already-built
prompt, never your repo — through the Oneport backend, which runs the model with
a server-side key and meters the tokens against the logged-in account.

`call_gemini` keeps its old signature so `triage.py` is unchanged; the `api_key`
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

from oneport_secrets.exceptions import AuthError, OneportError, RateLimitError

# Triage returns a JSON array of verdicts, so request raw JSON and disable
# thinking tokens (they'd otherwise risk truncating it on a thinking model).
_RESPONSE_JSON = True
_THINKING_BUDGET = 0
_TEMPERATURE = 0.0  # same finding → same verdict


def is_gemini_model(model: str) -> bool:
    return model.lower().startswith("gemini")


def call_gemini(
    model: str,
    api_key: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: int = 180,
) -> tuple[str, int]:
    """One metered completion via the Oneport proxy. Returns (text, total_tokens).

    Raises AuthError / RateLimitError / OneportError so the CLI handles it the
    same as before. `api_key` is ignored (the proxy holds the key)."""
    try:
        result = call_managed_llm(
            system=system,
            user=user,
            tool="secrets",
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

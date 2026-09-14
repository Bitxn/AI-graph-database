"""
Model access — now the Oneport managed proxy.

Impact's blast-radius engines (call graph, git co-change, ownership) run 100%
locally; only the final risk-verdict judgment is sent — the already-built facts
prompt, never your code — through the Oneport backend, which runs the model with
a server-side key and meters the tokens against the logged-in account.

`call_llm` keeps its old signature so `advisor.py` is unchanged; the `api_key`
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

from oneport_impact.exceptions import AuthError, ImpactError

# The advisor expects a JSON object, so request raw JSON and disable thinking
# tokens (they'd otherwise risk truncating the verdict on a thinking model).
_RESPONSE_JSON = True
_THINKING_BUDGET = 0
_TEMPERATURE = 0.0  # a risk verdict should be as deterministic as possible


def is_gemini_model(model: str) -> bool:
    return model.lower().startswith("gemini")


def call_llm(model: str, api_key: str, system: str, user: str,
             max_tokens: int, timeout: int = 120) -> tuple[str, int]:
    """One metered completion via the Oneport proxy. Returns (text, total_tokens).

    Raises AuthError / ImpactError (both caught by advisor.assess for graceful
    degradation). `api_key` is ignored (the proxy holds the key)."""
    try:
        result = call_managed_llm(
            system=system,
            user=user,
            tool="impact",
            action="assess",
            model=model,
            max_tokens=max_tokens,
            temperature=_TEMPERATURE,
            response_json=_RESPONSE_JSON,
            thinking_budget=_THINKING_BUDGET,
            timeout=timeout,
        )
    except (NotLoggedIn, OutOfTokens) as exc:
        raise AuthError(
            "Not logged in to Oneport (or out of tokens). Run "
            "`oneport-account login <token>` — free token at https://oneport.dev."
        ) from exc
    except (RateLimited, AccountError) as exc:
        raise ImpactError(f"Oneport service unavailable: {exc}") from exc

    return result.text, result.tokens_used

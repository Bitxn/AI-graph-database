from .config import DEFAULT_MODEL
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .redact import redact_text


def analyze_incident(
    logs: str = None,
    slack_thread: str = None,
    model: str = DEFAULT_MODEL,
    redact: bool = True,
) -> str:
    """Generate the post-mortem via the Oneport managed proxy (metered).

    Log/diff collection stays local; only the already-built prompt is sent
    through the Oneport backend, which runs the model with a server-side key and
    meters the tokens against the logged-in account.

    ``redact`` (default on) scrubs secrets/PII from the inputs before they leave
    the machine. It's idempotent, so a caller that already redacted (e.g. the
    CLI, which redacts up front to report what it stripped) can leave it on or
    pass ``redact=False`` without changing the result."""
    from oneport_account import (
        AccountError, NotLoggedIn, OutOfTokens, RateLimited, call_managed_llm,
    )

    if redact:
        logs = redact_text(logs).text if logs else logs
        slack_thread = redact_text(slack_thread).text if slack_thread else slack_thread

    user_prompt = build_user_prompt(logs=logs, slack_thread=slack_thread)
    try:
        result = call_managed_llm(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            tool="postmortem",
            action="analyze",
            model=model,
            max_tokens=2000,
            temperature=0.2,
        )
    except NotLoggedIn as exc:
        raise ValueError(
            "Not logged in to Oneport. Run `oneport-account login <token>` "
            "(free token at https://oneport.dev)."
        ) from exc
    except OutOfTokens as exc:
        raise ValueError(f"Out of Oneport tokens. Top up at {exc.buy_url}") from exc
    except (RateLimited, AccountError) as exc:
        raise RuntimeError(f"Model unavailable: {exc}") from exc

    return result.text
"""
oneport-account — the account & metering client shared by every Oneport tool.

Tools import `call_managed_llm` to run a billed LLM call through the Oneport
backend; the user's token balance is charged by the exact tokens used. No tool
ever holds a model key — only the user's revocable `op_live_...` token.
"""

from oneport_account.client import (
    LLMResult,
    call_managed_llm,
    fetch_balance,
    is_logged_in,
    redeem_code,
    validate_token,
)
from oneport_account.exceptions import (
    AccountError,
    NotLoggedIn,
    OutOfTokens,
    RateLimited,
)

__all__ = [
    "call_managed_llm", "fetch_balance", "redeem_code", "validate_token",
    "is_logged_in", "LLMResult",
    "AccountError", "NotLoggedIn", "OutOfTokens", "RateLimited",
]
__version__ = "0.3.0"

"""Account/metering errors. Tools catch these to show the right prompt."""

from __future__ import annotations


class AccountError(Exception):
    """Base error talking to the Oneport account service."""


class NotLoggedIn(AccountError):
    """No valid token on this machine — the user must `oneport-account login`."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message or (
            "Not logged in. Get a free token at https://oneport.dev, then run:\n"
            "  oneport-account login <op_live_...>"))


class OutOfTokens(AccountError):
    """The account balance is exhausted — carries a buy URL for the CLI to show."""

    def __init__(self, buy_url: str = "https://version-4-production.d2tx07mbxfs880.amplifyapp.com/pricing") -> None:
        self.buy_url = buy_url
        super().__init__(
            f"Out of tokens. Top up at {buy_url}, or redeem a code:\n"
            "  oneport-account redeem <CODE>")


class RateLimited(AccountError):
    """Upstream model rate limit — transient."""

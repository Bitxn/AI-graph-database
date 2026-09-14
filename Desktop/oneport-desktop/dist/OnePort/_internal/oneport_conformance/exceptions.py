"""Exception taxonomy — mirrors the rest of the OnePort suite."""

from __future__ import annotations


class OneportError(Exception):
    """Base for all expected, user-facing failures (exit code 2)."""


class AuthError(OneportError):
    """Not logged in, out of tokens, or a credential problem."""


class RateLimitError(OneportError):
    """The managed proxy is rate-limiting or quota-capped."""


class DiffError(OneportError):
    """Could not obtain a git diff to check."""


class IntentError(OneportError):
    """The intent document is missing, empty, or unreadable."""

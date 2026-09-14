"""
Custom exceptions for Oneport ApiDiff.

All exceptions inherit from ApidiffError so callers can catch the base class
for a broad handler or a specific subclass for targeted handling. Mirrors the
oneport-review exception hierarchy so the suite behaves consistently.
"""


class ApidiffError(Exception):
    """Base exception for all Oneport ApiDiff errors."""


class ConfigError(ApidiffError):
    """Raised when .oneportrc is malformed or a required config value is missing."""


class AuthError(ApidiffError):
    """Raised when the model API key is missing or invalid.

    The CLI catches this and prints an actionable message rather than a stack trace.
    """


class RateLimitError(ApidiffError):
    """Raised when the model API returns a 429 Too Many Requests response."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ParseError(ApidiffError):
    """Raised when a model response cannot be parsed. Includes the raw response."""

    def __init__(self, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


class IntegrationError(ApidiffError):
    """Raised when the GitHub integration fails (PR not found, bad token, ...)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DiffError(ApidiffError):
    """Raised when base/head file versions cannot be read or nothing changed."""

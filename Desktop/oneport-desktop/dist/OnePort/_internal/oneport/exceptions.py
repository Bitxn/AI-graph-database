"""
Custom exceptions for Oneport Review.

All exceptions inherit from OneportError so callers can catch the
base class for a broad handler or a specific subclass for targeted handling.
"""


class OneportError(Exception):
    """Base exception for all Oneport errors."""


class ConfigError(OneportError):
    """Raised when .oneportrc is malformed or a required config value is missing."""


class AuthError(OneportError):
    """
    Raised when the Anthropic API key is missing or invalid.

    The CLI catches this and prints an actionable message rather than a stack trace.
    """


class RateLimitError(OneportError):
    """
    Raised when the Anthropic API returns a 429 Too Many Requests response.

    Includes a `retry_after` attribute (seconds) when the API provides it.
    """

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ParseError(OneportError):
    """
    Raised when the Claude response cannot be parsed into a ReviewResult.

    Includes the raw response for debugging.
    """

    def __init__(self, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


class IntegrationError(OneportError):
    """
    Raised when a platform integration (GitHub, GitLab, Bitbucket) fails.

    E.g. PR not found, bad token, API unavailable.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DiffError(OneportError):
    """Raised when a diff cannot be read or is empty."""


class CacheError(OneportError):
    """Raised when the SQLite cache is corrupt or inaccessible. Non-fatal — reviewer falls back to live call."""

"""
Custom exceptions for Oneport Secrets.

All inherit from OneportError so the CLI can catch the base class for a broad
handler, or a specific subclass for targeted handling.
"""


class OneportError(Exception):
    """Base exception for all Oneport Secrets errors."""


class ConfigError(OneportError):
    """Raised when configuration or guidelines are malformed."""


class AuthError(OneportError):
    """Raised when the model API key is missing or invalid."""


class RateLimitError(OneportError):
    """Raised when the model API returns 429 Too Many Requests."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ParseError(OneportError):
    """Raised when a model response cannot be parsed. Carries the raw response."""

    def __init__(self, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


class ScanError(OneportError):
    """Raised when a scan target cannot be read (e.g. --history outside a git repo)."""


class IntegrationError(OneportError):
    """Raised when a platform integration (GitHub) fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

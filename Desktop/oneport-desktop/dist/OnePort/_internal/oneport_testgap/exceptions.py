"""
Custom exceptions for Oneport Testgap.

All exceptions inherit from OneportError so callers can catch the
base class for a broad handler or a specific subclass for targeted handling.
"""


class OneportError(Exception):
    """Base exception for all Oneport Testgap errors."""


class ConfigError(OneportError):
    """Raised when .oneportrc is malformed or a required config value is missing."""


class AuthError(OneportError):
    """
    Raised when the model API key is missing or invalid.

    The CLI catches this and prints an actionable message rather than a stack trace.
    """


class RateLimitError(OneportError):
    """
    Raised when the model API returns a 429 Too Many Requests response.

    Includes a `retry_after` attribute (seconds) when the API provides it.
    """

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ParseError(OneportError):
    """
    Raised when a model response cannot be parsed into the expected JSON shape.

    Includes the raw response for debugging.
    """

    def __init__(self, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


class IntegrationError(OneportError):
    """
    Raised when a platform integration (GitHub) fails.

    E.g. PR not found, bad token, API unavailable.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DiffError(OneportError):
    """Raised when a diff cannot be read."""


class NothingToAnalyze(DiffError):
    """
    Raised when there is legitimately nothing to do (empty diff, no changed
    Python lines). The CLI treats this as a graceful no-op (exit 0), not an
    error — a pre-ship gate must not fail a pipeline for having no changes.
    """


class CoverageError(OneportError):
    """Raised when the coverage run fails or coverage.xml cannot be read."""

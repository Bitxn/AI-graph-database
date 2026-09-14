"""
Custom exceptions for Oneport Costwatch.

All exceptions inherit from CostwatchError so callers can catch the base
class broadly, or a specific subclass for targeted handling. The CLI maps
each to an actionable message and exit code rather than a stack trace.
"""


class CostwatchError(Exception):
    """Base exception for all Costwatch errors."""


class ConfigError(CostwatchError):
    """Raised when configuration is malformed or a required value is missing."""


class AuthError(CostwatchError):
    """Raised when the model API key is missing or invalid."""


class RateLimitError(CostwatchError):
    """Raised when the model API returns a 429 Too Many Requests response."""


class ParseError(CostwatchError):
    """Raised when IaC or a model response cannot be parsed.

    Carries the offending raw text for debugging.
    """

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


class IntegrationError(CostwatchError):
    """Raised when a platform integration (e.g. GitHub) fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

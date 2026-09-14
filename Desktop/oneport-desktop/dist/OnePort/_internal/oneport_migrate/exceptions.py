"""
Custom exceptions for Oneport Migrate.

All exceptions inherit from OneportMigrateError so callers can catch the
base class for a broad handler or a specific subclass for targeted handling.
"""


class OneportMigrateError(Exception):
    """Base exception for all Oneport Migrate errors."""


class ConfigError(OneportMigrateError):
    """Raised when .oneportmigraterc is malformed or a required value is missing."""


class AuthError(OneportMigrateError):
    """Raised when the model API key is missing or invalid."""


class RateLimitError(OneportMigrateError):
    """Raised when the model API returns 429 Too Many Requests."""


class ParseError(OneportMigrateError):
    """
    Raised when a migration file cannot be parsed at all (syntax error,
    unrecognisable format). Includes the offending path for the CLI message.
    """

    def __init__(self, message: str, path: str = "") -> None:
        super().__init__(message)
        self.path = path


class IntegrationError(OneportMigrateError):
    """Raised when the GitHub integration fails (PR not found, bad token, ...)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

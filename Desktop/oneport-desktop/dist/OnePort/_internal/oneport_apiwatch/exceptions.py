"""Custom exceptions for oneport-apiwatch.

All inherit from ApiwatchError so callers can catch the base for a broad handler
or a subclass for targeted handling. The CLI maps these to actionable messages
and exit codes rather than stack traces.
"""


class ApiwatchError(Exception):
    """Base exception for all oneport-apiwatch errors."""


class ConfigError(ApiwatchError):
    """Raised when the checks file is missing, malformed, or invalid."""


class AuthError(ApiwatchError):
    """Raised when the Gemini API key is missing or invalid (only needed for --explain)."""


class RateLimitError(ApiwatchError):
    """Raised when the Gemini API returns 429 Too Many Requests."""


class AlertError(ApiwatchError):
    """Raised when an alert channel (Slack, GitHub issue) fails to deliver."""

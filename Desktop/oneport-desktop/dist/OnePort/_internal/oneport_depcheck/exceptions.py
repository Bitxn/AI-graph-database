"""Exception hierarchy, mirroring oneport-review so CLI handling is identical."""

from __future__ import annotations


class DepcheckError(Exception):
    """Base class for all oneport-depcheck errors."""


class ConfigError(DepcheckError):
    """Invalid or missing configuration."""


class AuthError(DepcheckError):
    """Model API key missing or rejected."""


class RateLimitError(DepcheckError):
    """Model API rate limit hit."""


class OSVError(DepcheckError):
    """OSV.dev API failure that prevents a trustworthy scan."""


class IntegrationError(DepcheckError):
    """GitHub (or other forge) API failure."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

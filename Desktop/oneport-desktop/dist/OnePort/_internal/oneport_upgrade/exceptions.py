"""Exception hierarchy — mapped to stable CLI exit codes."""

from __future__ import annotations


class UpgradeError(Exception):
    """Base recoverable error (CLI exit 1)."""


class ConfigError(UpgradeError):
    """Bad or missing configuration (CLI exit 2)."""


class AuthError(UpgradeError):
    """Missing/invalid model key when a plan is required (CLI exit 2)."""


class UnknownMigration(UpgradeError):
    """The requested --to target has no rule set (CLI exit 2)."""

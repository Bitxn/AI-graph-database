"""Exception hierarchy — mapped to stable CLI exit codes."""

from __future__ import annotations


class EvidenceError(Exception):
    """Base recoverable error (CLI exit 1)."""


class ConfigError(EvidenceError):
    """Bad or missing configuration (CLI exit 2)."""


class NoData(EvidenceError):
    """No ship history to build evidence from (CLI exit 2)."""


class UnknownFramework(EvidenceError):
    """The requested compliance framework isn't known (CLI exit 2)."""

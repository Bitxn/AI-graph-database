"""Exception hierarchy for oneport-impact. Mirrors the sibling tools so the CLI
can map each class to a stable exit code."""

from __future__ import annotations


class ImpactError(Exception):
    """Base class — a recoverable, reportable error (CLI exit 1)."""


class ConfigError(ImpactError):
    """Bad or missing configuration (CLI exit 2)."""


class AuthError(ImpactError):
    """Missing/invalid model API key when the LLM layer is required (CLI exit 2)."""


class GitError(ImpactError):
    """A git operation failed or the target isn't a git repository (CLI exit 2)."""


class ResolveError(ImpactError):
    """The requested symbol/file could not be found in the repo (CLI exit 2)."""


class IntegrationError(ImpactError):
    """A GitHub (or other platform) API call failed."""

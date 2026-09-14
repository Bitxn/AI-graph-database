"""Exception hierarchy — mapped to stable CLI exit codes."""

from __future__ import annotations


class StandupError(Exception):
    """Base recoverable error (CLI exit 1)."""


class ConfigError(StandupError):
    """Bad or missing configuration (CLI exit 2)."""


class AuthError(StandupError):
    """Missing/invalid model key when narration is required (CLI exit 2)."""


class GitError(StandupError):
    """A git operation failed or the target isn't a repo (CLI exit 2)."""


class NothingToReport(StandupError):
    """No commits in the requested range for the requested author (graceful)."""


class PostError(StandupError):
    """Delivering the report to an external destination (e.g. Slack) failed."""

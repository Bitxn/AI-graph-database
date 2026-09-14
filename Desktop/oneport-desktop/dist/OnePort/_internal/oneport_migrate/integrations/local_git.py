"""
Local git integration — resolves --staged / --head to changed migration files.

File contents are read from the working tree. For --head that's the committed
state as long as the tree is clean; for a dirty tree the working copy is what
would ship next anyway, so it is the right thing to gate on.
"""

from __future__ import annotations

import subprocess

from oneport_migrate.exceptions import OneportMigrateError


def _git_lines(args: list[str]) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            capture_output=True,
            # Git emits UTF-8; without this, text=True uses the OS locale (cp1252
            # on Windows) and any emoji/non-Latin-1 byte crashes the decode in a
            # reader thread, leaving stdout=None. See the FastAPI 🔖 case.
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except FileNotFoundError as exc:
        raise OneportMigrateError("git is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise OneportMigrateError(
            f"git {' '.join(args)} failed: {exc.stderr.strip() or exc.stdout.strip()}"
        ) from exc
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def staged_files() -> list[str]:
    """Paths staged for commit (Added/Copied/Modified/Renamed — not deleted)."""
    return _git_lines(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])


def head_files() -> list[str]:
    """Paths changed in the last commit."""
    return _git_lines(["diff", "HEAD~1..HEAD", "--name-only", "--diff-filter=ACMR"])

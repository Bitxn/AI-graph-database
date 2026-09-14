"""
Local git diff integration.

Produces diffs from the local git repository for review without needing
a remote platform. Useful for pre-commit hooks and local dev workflows.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from oneport.exceptions import DiffError


class LocalDiffIntegration:
    def __init__(self, repo_path: str | Path | None = None) -> None:
        self.repo_path = str(repo_path or Path.cwd())

    def get_staged_diff(self) -> str:
        """Return the diff of all staged (git add'd) changes."""
        return self._run_git("diff", "--cached", "--unified=5")

    def get_head_diff(self) -> str:
        """Return the diff of the last commit (HEAD~1..HEAD)."""
        return self._run_git("diff", "HEAD~1", "HEAD", "--unified=5")

    def get_branch_diff(self, base: str = "main") -> str:
        """Return the diff between the current branch and a base branch."""
        return self._run_git("diff", base, "--unified=5")

    def get_file_diff(self, file_path: str) -> str:
        """Return the diff for a single unstaged file."""
        return self._run_git("diff", "--unified=5", "--", file_path)

    # ── Private ────────────────────────────────────────────────────────────────

    def _run_git(self, *args: str) -> str:
        cmd = ["git", "-C", self.repo_path, *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                # Git emits UTF-8. WITHOUT this, text=True decodes with the OS
                # locale — cp1252 on Windows — and any emoji/non-Latin-1 byte in
                # the diff (FastAPI puts 🔖 in its release notes) blows up the
                # decoder in subprocess's reader THREAD, which silently leaves
                # stdout=None while returncode stays 0. The caller then does
                # None.strip() → AttributeError, miles from the real cause.
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise DiffError("git diff timed out after 30 seconds.") from exc
        except FileNotFoundError as exc:
            raise DiffError("git not found. Ensure git is installed and on PATH.") from exc

        if result.returncode not in (0, 1):  # git diff returns 1 when there are differences
            raise DiffError(f"git diff failed: {(result.stderr or '').strip()}")

        diff = (result.stdout or "").strip()
        if not diff:
            raise DiffError(
                "No diff found. "
                "Make sure you have staged changes (git add) or committed code."
            )
        return diff

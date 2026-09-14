"""
Local git diff — the change we check against intent.

UTF-8 is forced on purpose: git emits UTF-8, and without this, text=True decodes
with the OS locale (cp1252 on Windows) so any emoji / non-Latin-1 byte in the
diff crashes subprocess's reader thread, leaving stdout=None while returncode
stays 0 — an AttributeError miles from the real cause.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from oneport_conformance.exceptions import DiffError

# git's canonical empty-tree object — lets the very first commit still diff
# (HEAD~1 doesn't exist yet). Generalises to any repo shape, never per-repo.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _run_git(repo: str, *args: str) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", repo, *args]
    try:
        return subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=30
        )
    except subprocess.TimeoutExpired as exc:
        raise DiffError("git timed out after 30s.") from exc
    except FileNotFoundError as exc:
        raise DiffError("git not found. Ensure git is installed and on PATH.") from exc


def get_diff(repo: str | Path | None = None, mode: str = "head",
             base: str = "main") -> str:
    """Return a unified diff for the requested change set.

    mode: "head" (last commit, robust to a single-commit repo), "staged"
    (git add'd changes), or "branch" (current branch vs `base`).
    Raises DiffError on a genuine git failure; returns "" when there is simply
    nothing to check (an empty diff is not an error — it's 'nothing changed').
    """
    repo = str(repo or Path.cwd())

    if mode == "staged":
        proc = _run_git(repo, "diff", "--cached", "--unified=5")
    elif mode == "branch":
        proc = _run_git(repo, "diff", base, "--unified=5")
    else:  # head
        proc = _run_git(repo, "diff", "HEAD~1", "HEAD", "--unified=5")
        # Single-commit repo: HEAD~1 is unknown → diff against the empty tree so
        # the first commit is still fully checkable instead of crashing.
        if proc.returncode not in (0, 1):
            proc = _run_git(repo, "diff", _EMPTY_TREE, "HEAD", "--unified=5")

    if proc.returncode not in (0, 1):  # 1 == "there are differences", not a failure
        raise DiffError(f"git diff failed: {(proc.stderr or '').strip()[:300]}")

    return (proc.stdout or "").strip()

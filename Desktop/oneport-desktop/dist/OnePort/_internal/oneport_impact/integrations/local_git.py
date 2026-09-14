"""
Thin wrapper over the local `git` CLI. All git access in oneport-impact goes
through here so error handling and path normalisation live in one place.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from oneport_impact.exceptions import GitError


def run_git(root: str | Path, *args: str, timeout: int = 30) -> str:
    """Run a git command in `root`; return stdout. Raises GitError on failure."""
    cmd = ["git", "-C", str(root), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise GitError("git not found — install git and ensure it's on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git timed out after {timeout}s: git {' '.join(args)}") from exc
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def is_repo(root: str | Path) -> bool:
    try:
        return run_git(root, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except GitError:
        return False


def require_repo(root: str | Path) -> None:
    if not is_repo(root):
        raise GitError(
            f"{root} is not inside a git repository. oneport-impact mines history "
            "and ownership from git — run it inside a repo."
        )


def current_branch(root: str | Path) -> str:
    try:
        return run_git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except GitError:
        return ""


def current_commit(root: str | Path, short: bool = True) -> str:
    try:
        args = ["rev-parse", "--short", "HEAD"] if short else ["rev-parse", "HEAD"]
        return run_git(root, *args).strip()
    except GitError:
        return ""


def changed_files(root: str | Path, mode: str) -> list[str]:
    """Repo-relative paths changed in the given mode: 'staged' | 'head'."""
    if mode == "staged":
        out = run_git(root, "diff", "--cached", "--name-only")
    elif mode == "head":
        out = run_git(root, "diff", "--name-only", "HEAD~1", "HEAD")
    else:
        raise GitError(f"unknown change mode: {mode}")
    return [ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()]


def diff_text(root: str | Path, mode: str) -> str:
    if mode == "staged":
        return run_git(root, "diff", "--cached", "--unified=3")
    if mode == "head":
        return run_git(root, "diff", "--unified=3", "HEAD~1", "HEAD")
    raise GitError(f"unknown change mode: {mode}")

"""Thin wrapper over the local `git` CLI — the only source of facts this tool uses."""

from __future__ import annotations

import subprocess
from pathlib import Path

from oneport_standup.exceptions import GitError


def run_git(root: str | Path, *args: str, timeout: int = 30) -> str:
    cmd = ["git", "-C", str(root), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise GitError("git not found — install git and ensure it's on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git timed out after {timeout}s") from exc
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
        raise GitError(f"{root} is not a git repository. oneport-standup reads your git history.")


def current_email(root: str | Path) -> str:
    try:
        return run_git(root, "config", "user.email").strip()
    except GitError:
        return ""


def current_name(root: str | Path) -> str:
    try:
        return run_git(root, "config", "user.name").strip()
    except GitError:
        return ""

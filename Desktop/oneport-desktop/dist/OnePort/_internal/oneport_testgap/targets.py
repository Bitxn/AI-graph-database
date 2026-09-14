"""
Target resolution — turn the CLI TARGET into a unified diff.

Accepted targets (mirrors oneport-review):
  --staged                 staged changes (git diff --cached)
  --head                   last commit (git diff HEAD~1 HEAD)
  a GitHub PR URL          https://github.com/org/repo/pull/42

Coverage is ALWAYS computed locally (the working tree is what pytest runs
against), so PR-URL targets are meant for CI where the PR branch is checked
out and only the diff comes from GitHub.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from oneport_testgap.exceptions import DiffError, NothingToAnalyze
from oneport_testgap.integrations.github import GitHubIntegration, parse_pr_url


@dataclass
class ResolvedTarget:
    """A diff plus, for PR targets, enough to post the report back."""

    diff: str
    label: str
    # Populated only for GitHub PR targets: {"owner", "repo", "number", "head_sha"}
    pr_ref: dict = field(default_factory=dict)


def is_pr_url(target: str) -> bool:
    return target.startswith(("http://", "https://")) and "/pull/" in target


def resolve_target(target: str, repo_path: str | Path | None = None) -> ResolvedTarget:
    """Resolve a CLI target into a unified diff. Raises DiffError when empty."""
    if is_pr_url(target):
        gh = GitHubIntegration()
        pr = gh.get_pr(target)
        owner, repo, number = parse_pr_url(target)
        if not pr.diff.strip():
            raise NothingToAnalyze(f"PR #{number} has an empty diff.")
        return ResolvedTarget(
            diff=pr.diff,
            label=target,
            pr_ref={"owner": owner, "repo": repo, "number": number, "head_sha": pr.head_sha},
        )

    local = LocalDiffIntegration(repo_path)
    if target == "--head":
        return ResolvedTarget(diff=local.get_head_diff(), label="HEAD~1..HEAD")
    # Default: staged changes.
    return ResolvedTarget(diff=local.get_staged_diff(), label="staged changes")


class LocalDiffIntegration:
    """Local git diff integration (mirrors oneport-review's)."""

    def __init__(self, repo_path: str | Path | None = None) -> None:
        self.repo_path = str(repo_path or Path.cwd())

    def get_staged_diff(self) -> str:
        """Return the diff of all staged (git add'd) changes."""
        return self._run_git("diff", "--cached", "--unified=5")

    def get_head_diff(self) -> str:
        """Return the diff of the last commit (HEAD~1..HEAD)."""
        return self._run_git("diff", "HEAD~1", "HEAD", "--unified=5")

    # ── Private ────────────────────────────────────────────────────────────────

    def _run_git(self, *args: str) -> str:
        cmd = ["git", "-C", self.repo_path, *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                # Git emits UTF-8. Without an explicit encoding, text=True decodes
                # with the OS locale (cp1252 on Windows) and any emoji/non-Latin-1
                # byte in the diff crashes the decoder in subprocess's reader
                # THREAD — leaving stdout=None with returncode 0, so the caller
                # hits None.strip() far from the real cause.
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
            raise NothingToAnalyze(
                "No diff found. "
                "Make sure you have staged changes (git add) or committed code."
            )
        return diff

"""
Local git sources — base vs head file contents for the three local modes.

Unlike a plain `git diff`, ApiDiff needs the FULL base and head text of every
changed file: the AST differ compares whole public surfaces, not hunks.

Modes (mirroring oneport-review's CLI):
  --staged      index vs HEAD           (pre-commit gate)
  --head        HEAD vs HEAD~1          (post-commit / CI on push)
  --base BRANCH working tree vs the merge-base with BRANCH (PR-style local check)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from oneport_apidiff.exceptions import DiffError


@dataclass
class FilePair:
    """One changed file: full text before and after. None = absent on that side."""

    path: str  # repo-relative, forward slashes
    base: str | None
    head: str | None


class LocalGitSource:
    def __init__(self, repo_path: str | Path | None = None) -> None:
        self.repo_path = str(repo_path or Path.cwd())

    # ── Modes ──────────────────────────────────────────────────────────────────

    def staged_pairs(self) -> list[FilePair]:
        """Staged (git add'd) changes: HEAD vs the index."""
        return self._pairs_from_name_status(
            self._run_git("diff", "--cached", "--name-status", "-M"),
            base_ref="HEAD",
            head_ref=":",  # ":path" = staged blob
        )

    def head_pairs(self) -> list[FilePair]:
        """The last commit: HEAD~1 vs HEAD."""
        return self._pairs_from_name_status(
            self._run_git("diff", "--name-status", "-M", "HEAD~1", "HEAD"),
            base_ref="HEAD~1",
            head_ref="HEAD",
        )

    def base_pairs(self, base_branch: str = "main") -> list[FilePair]:
        """Working tree vs the merge-base with `base_branch` (what a PR would show)."""
        merge_base = self._run_git("merge-base", base_branch, "HEAD").strip()
        if not merge_base:
            raise DiffError(f"Could not find a merge base with '{base_branch}'.")
        return self._pairs_from_name_status(
            self._run_git("diff", "--name-status", "-M", merge_base),
            base_ref=merge_base,
            head_ref=None,  # None = read from the working tree
        )

    # ── Internals ──────────────────────────────────────────────────────────────

    def _pairs_from_name_status(
        self, name_status: str, base_ref: str, head_ref: str | None
    ) -> list[FilePair]:
        pairs: list[FilePair] = []
        for line in name_status.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0].strip()
            if status.startswith("R") and len(parts) >= 3:
                # A rename is a removal at the old path + an addition at the new
                # one — that's exactly how consumers experience it (imports break).
                old_path, new_path = parts[1], parts[2]
                pairs.append(
                    FilePair(path=old_path, base=self._show(base_ref, old_path), head=None)
                )
                pairs.append(
                    FilePair(path=new_path, base=None, head=self._read_head(head_ref, new_path))
                )
                continue

            path = parts[1]
            base = None if status.startswith("A") else self._show(base_ref, path)
            head = None if status.startswith("D") else self._read_head(head_ref, path)
            pairs.append(FilePair(path=path, base=base, head=head))

        if not pairs:
            raise DiffError(
                "No changed files found. Make sure you have staged changes "
                "(git add) or committed code."
            )
        return pairs

    def _read_head(self, head_ref: str | None, path: str) -> str | None:
        if head_ref is None:
            file_path = Path(self.repo_path) / path
            if not file_path.exists():
                return None
            return file_path.read_text(encoding="utf-8", errors="replace")
        if head_ref == ":":
            return self._show_raw(f":{path}")
        return self._show(head_ref, path)

    def _show(self, ref: str, path: str) -> str | None:
        return self._show_raw(f"{ref}:{path}")

    def _show_raw(self, spec: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", self.repo_path, "show", spec],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise DiffError("git show timed out after 30 seconds.") from exc
        except FileNotFoundError as exc:
            raise DiffError("git not found. Ensure git is installed and on PATH.") from exc
        if result.returncode != 0:
            return None  # path absent at that ref (added/deleted file)
        return result.stdout

    def _run_git(self, *args: str) -> str:
        cmd = ["git", "-C", self.repo_path, *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise DiffError("git command timed out after 30 seconds.") from exc
        except FileNotFoundError as exc:
            raise DiffError("git not found. Ensure git is installed and on PATH.") from exc

        if result.returncode not in (0, 1):  # git diff returns 1 when there are differences
            raise DiffError(f"git failed: {result.stderr.strip()}")
        return result.stdout

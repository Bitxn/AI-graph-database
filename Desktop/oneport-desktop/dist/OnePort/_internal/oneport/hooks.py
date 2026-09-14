"""
Git hook installation — catch issues before the commit even exists.

`oneport install-hooks` writes a pre-commit hook that reviews staged changes
and blocks the commit on error/critical findings. Escape hatch for emergencies:

    ONEPORT_SKIP=1 git commit ...
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

HOOK_MARKER = "# installed by oneport install-hooks"

PRE_COMMIT_HOOK = f"""\
#!/bin/sh
{HOOK_MARKER}
# Reviews staged changes with Oneport before every commit.
# Skip once in an emergency:  ONEPORT_SKIP=1 git commit ...

if [ "$ONEPORT_SKIP" = "1" ]; then
    echo "oneport: skipped (ONEPORT_SKIP=1)"
    exit 0
fi

oneport review --staged
status=$?
if [ $status -ne 0 ]; then
    echo ""
    echo "oneport: commit blocked by review findings above."
    echo "oneport: fix them, or bypass once with: ONEPORT_SKIP=1 git commit ..."
fi
exit $status
"""


def find_git_dir(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default cwd) to find the .git directory."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / ".git"
        if candidate.is_dir():
            return candidate
    return None


def install_pre_commit_hook(git_dir: Path, force: bool = False) -> Path:
    """
    Write the pre-commit hook into `git_dir`/hooks.

    Refuses to clobber a hook we didn't install unless `force` is set, in which
    case the old hook is preserved as pre-commit.bak.

    Returns the hook path written.

    Raises:
        FileExistsError: a foreign pre-commit hook exists and force is False.
    """
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-commit"

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8", errors="replace")
        if HOOK_MARKER in existing:
            pass  # ours — safe to refresh in place
        elif force:
            hook_path.replace(hook_path.with_suffix(".bak"))
        else:
            raise FileExistsError(
                f"A pre-commit hook already exists at {hook_path} and wasn't "
                "installed by Oneport. Re-run with --force to back it up to "
                "pre-commit.bak and replace it, or add `oneport review --staged` "
                "to your existing hook manually."
            )

    hook_path.write_text(PRE_COMMIT_HOOK, encoding="utf-8", newline="\n")

    # Git requires the hook to be executable on POSIX. No-op on Windows,
    # where Git for Windows runs hooks through sh regardless.
    if os.name != "nt":
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return hook_path

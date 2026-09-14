"""
Team guidelines — the same `.oneport/guidelines.md` file oneport-review uses.

ApiDiff injects the file into the classification prompt so verdicts respect
team policy, e.g. "internal package, private API — only flag __all__ symbols"
or "our CLI flags are public API too". Detection stays deterministic either
way; guidelines only influence what a change *means*.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_GUIDELINES_PATH = ".oneport/guidelines.md"


def load_guidelines(
    repo_root: str | Path = ".",
    path: str | Path = DEFAULT_GUIDELINES_PATH,
) -> str:
    """Return the guidelines file content, or "" if it doesn't exist yet."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(repo_root) / p
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()

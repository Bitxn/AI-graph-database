"""
Team guidelines — project-specific rules shared across the Oneport suite.

Guidelines live in a plain markdown file inside the repo (default:
`.oneport/guidelines.md`) — the same file oneport-review learns into. Testgap
injects the file into its ranking and generation prompts, so testing rules
("all payment code needs property-based tests", "use pytest fixtures, never
setUp") shape both which gaps rank highest and what the generated tests
look like.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_GUIDELINES_PATH = ".oneport/guidelines.md"


def load_guidelines(path: str | Path = DEFAULT_GUIDELINES_PATH) -> str:
    """Return the guidelines file content, or "" if it doesn't exist yet."""
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()

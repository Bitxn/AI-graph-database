"""
Team guidelines — project-specific rules Costwatch respects on every run.

Guidelines live in a plain markdown file in the repo (default
`.oneport/guidelines.md`), so they're version-controlled and shared by the
whole team. Each entry is injected into the prompt and honoured by the waste
detector — e.g. "prod must stay on-demand, ignore spot suggestions there" or
"the data-warehouse box is intentionally large, don't flag it".
"""

from __future__ import annotations

from pathlib import Path

from costwatch.config import DEFAULT_GUIDELINES_PATH


def load_guidelines(path: str | Path = DEFAULT_GUIDELINES_PATH) -> str:
    """Return the guidelines file content, or "" if it doesn't exist yet."""
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace").strip()

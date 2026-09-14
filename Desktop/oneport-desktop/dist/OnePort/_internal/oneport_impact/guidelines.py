"""
Team guidelines loader — the shared `.oneport/guidelines.md` every Oneport tool
reads. For impact, its free-text lines are passed to the risk-verdict prompt as
context ("our payments module is business-critical; treat changes there as high
risk"), so the judgment reflects the team's own priorities.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_GUIDELINES_PATH = ".oneport/guidelines.md"


def load_guidelines(path: str | Path = DEFAULT_GUIDELINES_PATH) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""

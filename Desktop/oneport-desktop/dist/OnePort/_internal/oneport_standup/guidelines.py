"""Shared `.oneport/guidelines.md` loader — its text is passed to the narrator as
tone/context (e.g. 'write standups in past tense, no fluff')."""

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

"""
Team guidelines — project-specific context the LLM layer must respect.

Guidelines live in a plain markdown file inside the repo (default:
`.oneport/guidelines.md` — the SAME file oneport-review uses, so one file
teaches the whole Oneport suite). Example entries relevant to migrations:

  - our users table is small (<10k rows), downgrade lock severity
  - the events table is append-only and 400M rows — never rewrite it in place

Guidelines are injected into the blast-radius prompt. The LLM may adjust a
finding's severity ONLY when a guideline explicitly justifies it, quoting the
guideline in its reason. Detection itself is never affected — the
deterministic rule engine does not read this file.
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

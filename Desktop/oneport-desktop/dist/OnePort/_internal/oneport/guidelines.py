"""
Team guidelines — project-specific review rules that Oneport learns over time.

Guidelines live in a plain markdown file inside the repo (default:
`.oneport/guidelines.md`), so they're version-controlled, code-reviewed like
any other change, and shared by the whole team automatically. Every entry is
injected into the review prompt and enforced like the built-in rule catalog.

Entries come from:
  - `oneport learn "never use print() in library code"`  (CLI)
  - "@oneport remember: ..." replies in PR threads        (chat responder)
  - hand-editing the file, which is just markdown
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

DEFAULT_GUIDELINES_PATH = ".oneport/guidelines.md"

FILE_HEADER = """\
# Team review guidelines

Project-specific rules enforced by Oneport Review on every review, in addition
to the built-in rule catalog. Add entries with `oneport learn "..."`, by
replying `@oneport remember: ...` on a PR, or by editing this file directly.
"""


def load_guidelines(path: str | Path = DEFAULT_GUIDELINES_PATH) -> str:
    """Return the guidelines file content, or "" if it doesn't exist yet."""
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()


def append_guideline(
    text: str,
    path: str | Path = DEFAULT_GUIDELINES_PATH,
    source: str = "cli",
) -> Path:
    """
    Append one guideline entry, creating the file (with header) if needed.

    Returns the path written, so callers can tell the user where it went.
    """
    text = " ".join(text.split())  # collapse newlines/extra spaces to one line
    if not text:
        raise ValueError("Guideline text is empty.")

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    entry = f"- {text}  <!-- added {date.today().isoformat()} via {source} -->\n"
    if p.exists():
        existing = p.read_text(encoding="utf-8")
        joiner = "" if existing.endswith("\n") else "\n"
        p.write_text(existing + joiner + entry, encoding="utf-8")
    else:
        p.write_text(FILE_HEADER + "\n" + entry, encoding="utf-8")
    return p

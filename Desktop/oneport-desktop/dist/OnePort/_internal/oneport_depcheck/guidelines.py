"""
Team guidelines — same file and conventions as oneport-review.

Guidelines live in `.oneport/guidelines.md`, version-controlled with the repo.
The whole file is injected into the triage prompt as context (e.g.
"dev-dependency CVEs are warn-only" — which matches depcheck's built-in gating:
DEV-ONLY findings never fail `--fail-on reachable`).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

DEFAULT_GUIDELINES_PATH = ".oneport/guidelines.md"

FILE_HEADER = """\
# Team review guidelines

Project-specific rules applied by Oneport tools. Add entries with
`oneport-depcheck learn "..."` or by editing this file directly.
"""


def load_guidelines(root: str | Path = ".", path: str = DEFAULT_GUIDELINES_PATH) -> str:
    """Return the guidelines file content, or "" if it doesn't exist yet."""
    p = Path(root) / path
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()


def append_guideline(
    text: str,
    root: str | Path = ".",
    path: str = DEFAULT_GUIDELINES_PATH,
    source: str = "cli",
) -> Path:
    """Append one guideline entry, creating the file (with header) if needed."""
    text = " ".join(text.split())
    if not text:
        raise ValueError("Guideline text is empty.")

    p = Path(root) / path
    p.parent.mkdir(parents=True, exist_ok=True)

    entry = f"- {text}  <!-- added {date.today().isoformat()} via {source} -->\n"
    if p.exists():
        existing = p.read_text(encoding="utf-8")
        joiner = "" if existing.endswith("\n") else "\n"
        p.write_text(existing + joiner + entry, encoding="utf-8")
    else:
        p.write_text(FILE_HEADER + "\n" + entry, encoding="utf-8")
    return p

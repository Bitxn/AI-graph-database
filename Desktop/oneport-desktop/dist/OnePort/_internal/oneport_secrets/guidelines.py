"""
Team guidelines for Oneport Secrets — a version-controlled markdown file
(default `.oneport/guidelines.md`) that customises the scanner:

  - `regex: PATTERN`        add a custom high-severity detector (e.g. an internal
                            token format GitGuardian doesn't know)
  - `ignore: path/glob`     never scan matching paths

Lines that aren't directives are ignored, so the file doubles as human notes.
Same file is shared by the whole team via git — no server, no dashboard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from oneport_secrets.detectors import Detector

DEFAULT_GUIDELINES_PATH = ".oneport/guidelines.md"

FILE_HEADER = """\
# Oneport Secrets guidelines

Project-specific scanner rules, version-controlled and shared by the whole team.
Directives (anywhere in the file):

    - regex: MYCORP_[A-Z0-9]{32}     # a custom secret format to detect
    - ignore: tests/fixtures/         # never scan these paths

Add entries with `oneport-secrets learn "regex: ..."` or by editing this file.
"""

_REGEX_DIRECTIVE = re.compile(r"^\s*-?\s*regex:\s*(?P<pat>.+?)\s*$", re.IGNORECASE)
_IGNORE_DIRECTIVE = re.compile(r"^\s*-?\s*ignore:\s*(?P<path>.+?)\s*$", re.IGNORECASE)


@dataclass
class Guidelines:
    custom_detectors: list[Detector] = field(default_factory=list)
    ignore_paths: list[str] = field(default_factory=list)
    raw: str = ""


def _strip_inline_comment(value: str) -> str:
    # Allow a trailing "# note" on a directive line.
    if "#" in value:
        value = value.split("#", 1)[0]
    return value.strip().strip("'\"")


def load_guidelines(path: str | Path = DEFAULT_GUIDELINES_PATH) -> Guidelines:
    p = Path(path)
    if not p.exists():
        return Guidelines()
    text = p.read_text(encoding="utf-8")
    guidelines = Guidelines(raw=text.strip())

    for i, line in enumerate(text.splitlines()):
        if m := _REGEX_DIRECTIVE.match(line):
            pat = _strip_inline_comment(m.group("pat"))
            if not pat:
                continue
            try:
                compiled = re.compile(pat)
            except re.error:
                continue  # a malformed custom regex must never crash a scan
            guidelines.custom_detectors.append(Detector(
                id=f"custom-{i}",
                name="Custom rule",
                severity="high",
                pattern=compiled,
                description=f"custom regex from {p}",
            ))
        elif m := _IGNORE_DIRECTIVE.match(line):
            ipath = _strip_inline_comment(m.group("path"))
            if ipath:
                guidelines.ignore_paths.append(ipath)

    return guidelines


def append_guideline(text: str, path: str | Path = DEFAULT_GUIDELINES_PATH, source: str = "cli") -> Path:
    """Append one directive line, creating the file (with header) if needed."""
    text = " ".join(text.split())
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

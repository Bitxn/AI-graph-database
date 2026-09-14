"""
Hidden HTML markers embedded in posted GitHub content.

The sticky summary comment carries a marker so a re-run edits the same comment
in place instead of stacking a new one per push — the PR is the state store.
"""

from __future__ import annotations

import re

STICKY_MARKER = "<!-- oneport-depcheck-summary -->"

SCANNED_MARKER_TPL = "<!-- oneport-depcheck-scanned: {sha} -->"
SCANNED_MARKER_RE = re.compile(
    r"<!-- oneport-depcheck-scanned: (?P<sha>[0-9a-fA-F]{7,40}) -->"
)


def extract_scanned_sha(body: str) -> str | None:
    match = SCANNED_MARKER_RE.search(body or "")
    return match.group("sha") if match else None

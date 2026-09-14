"""
Hidden HTML markers embedded in posted GitHub content.

Markers let a later run find state left by an earlier run without any server
or database: the PR itself is the state store. On re-run, if the PR head SHA
matches the last posted marker, the duplicate review is skipped.
"""

from __future__ import annotations

import re

REVIEWED_MARKER_TPL = "<!-- oneport-migrate-reviewed: {sha} -->"
REVIEWED_MARKER_RE = re.compile(
    r"<!-- oneport-migrate-reviewed: (?P<sha>[0-9a-fA-F]{7,40}) -->"
)


def extract_reviewed_sha(body: str) -> str | None:
    """Pull the reviewed-SHA out of a review body, or None."""
    match = REVIEWED_MARKER_RE.search(body or "")
    return match.group("sha") if match else None

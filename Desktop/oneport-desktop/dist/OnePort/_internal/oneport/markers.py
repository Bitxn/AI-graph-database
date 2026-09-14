"""
Hidden HTML markers embedded in posted GitHub content.

Markers let a later run find state left by an earlier run without any server
or database: the PR itself is the state store.
"""

from __future__ import annotations

import re

# Appended to every posted review body; next run reads it to know which head
# SHA was last reviewed, and reviews only the commits since.
REVIEWED_MARKER_TPL = "<!-- oneport-reviewed: {sha} -->"
REVIEWED_MARKER_RE = re.compile(r"<!-- oneport-reviewed: (?P<sha>[0-9a-fA-F]{7,40}) -->")


def extract_reviewed_sha(body: str) -> str | None:
    """Pull the reviewed-SHA out of a review body, or None."""
    match = REVIEWED_MARKER_RE.search(body or "")
    return match.group("sha") if match else None

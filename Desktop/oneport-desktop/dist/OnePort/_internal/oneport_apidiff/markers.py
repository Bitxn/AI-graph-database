"""
Hidden HTML markers embedded in posted GitHub content.

Markers let a later run find state left by an earlier run without any server
or database: the PR itself is the state store. Same trick as oneport-review's
oneport/markers.py, with apidiff-specific marker names so both tools can post
on the same PR without clobbering each other.
"""

from __future__ import annotations

import re

# Identifies the sticky verdict-table comment, so re-runs edit it in place
# instead of stacking a new comment per push.
STICKY_MARKER = "<!-- oneport-apidiff-sticky -->"

# Stamped with the head SHA that was checked; a re-run against the same SHA
# skips posting a duplicate inline review.
CHECKED_MARKER_TPL = "<!-- oneport-apidiff-checked: {sha} -->"
CHECKED_MARKER_RE = re.compile(r"<!-- oneport-apidiff-checked: (?P<sha>[0-9a-fA-F]{7,40}) -->")


def extract_checked_sha(body: str) -> str | None:
    """Pull the checked-SHA out of a comment body, or None."""
    match = CHECKED_MARKER_RE.search(body or "")
    return match.group("sha") if match else None

"""
Hidden HTML markers embedded in posted GitHub content.

Markers let a later run find state left by an earlier run without any server
or database: the PR itself is the state store. Testgap uses one marker to keep
its gap report as a single always-current sticky review body per PR.
"""

from __future__ import annotations

import re

# Appended to every posted gap report; identifies Testgap's own report so a
# re-run on a new push can be recognised (and tooling can find/filter it).
TESTGAP_MARKER_TPL = "<!-- oneport-testgap: {sha} -->"
TESTGAP_MARKER_RE = re.compile(r"<!-- oneport-testgap: (?P<sha>[0-9a-fA-F]{7,40}) -->")


def extract_analyzed_sha(body: str) -> str | None:
    """Pull the analyzed-SHA out of a report body, or None."""
    match = TESTGAP_MARKER_RE.search(body or "")
    return match.group("sha") if match else None

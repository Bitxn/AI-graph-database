"""
Unified-diff parsing, ported from oneport-review's oneport/diff_utils.py.

GitHub's Pull Request Reviews API rejects an inline comment whose `line` isn't
part of a diff hunk (422 "pull_request_review_thread.line must be part of the
diff"). Before anchoring a comment to a changed signature we need to know, per
file, which new-file line numbers are actually visible in the PR's "Files
changed" tab.
"""

from __future__ import annotations

import re
from collections import defaultdict

_FILE_HEADER_RE = re.compile(r"^\+\+\+ b/(?P<path>.+)$")
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@")


def commentable_lines(diff_text: str) -> dict[str, set[int]]:
    """
    Parse a unified diff and return {file_path: {line_numbers}} for every
    new-file line number that appears inside a hunk.

    A line is commentable if it's a "+" (added) or " " (context) line within a
    hunk — those exist in the new file at a known line number. "-" (deleted)
    lines don't exist in the new file and are never commentable.
    """
    result: dict[str, set[int]] = defaultdict(set)
    current_file: str | None = None
    new_line = 0
    in_hunk = False

    for raw_line in diff_text.splitlines():
        file_match = _FILE_HEADER_RE.match(raw_line)
        if file_match:
            current_file = file_match.group("path")
            in_hunk = False
            continue

        hunk_match = _HUNK_HEADER_RE.match(raw_line)
        if hunk_match:
            new_line = int(hunk_match.group("new_start"))
            in_hunk = True
            continue

        if not in_hunk or current_file is None:
            continue

        if raw_line.startswith("-"):
            continue  # deleted line — doesn't exist in the new file
        if raw_line.startswith("+") or raw_line.startswith(" "):
            result[current_file].add(new_line)
            new_line += 1
        elif raw_line.startswith("\\"):
            continue  # "\ No newline at end of file" — doesn't consume a line
        else:
            # Diff header we don't recognise (e.g. "diff --git ..." for the
            # next file) — stop trusting the line counter until the next hunk.
            in_hunk = False

    return dict(result)

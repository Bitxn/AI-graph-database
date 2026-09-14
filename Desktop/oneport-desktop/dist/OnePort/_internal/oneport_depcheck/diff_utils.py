"""
Unified-diff helpers — which (file, new-line-number) pairs GitHub will accept
an inline review comment on. Same contract as oneport-review/diff_utils.
"""

from __future__ import annotations

import re

_FILE_RE = re.compile(r"^\+\+\+ b/(?P<path>.+)$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")


def commentable_lines(diff_text: str) -> dict[str, set[int]]:
    """Map file path → set of new-file line numbers present in the diff."""
    result: dict[str, set[int]] = {}
    current_file: str | None = None
    in_hunk = False
    new_line = 0

    for line in diff_text.splitlines():
        if line.startswith("diff "):
            in_hunk = False
            continue
        file_match = _FILE_RE.match(line)
        if file_match:
            current_file = file_match.group("path")
            result.setdefault(current_file, set())
            in_hunk = False
            continue
        hunk_match = _HUNK_RE.match(line)
        if hunk_match:
            new_line = int(hunk_match.group("start"))
            in_hunk = True
            continue
        if current_file is None or not in_hunk:
            continue
        if line.startswith("+"):
            result[current_file].add(new_line)
            new_line += 1
        elif line.startswith("-") or line.startswith("\\"):
            continue  # removed line / "\ No newline at end of file"
        else:  # context line (starts with " ", or is empty)
            result[current_file].add(new_line)
            new_line += 1

    return result

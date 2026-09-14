"""
Utilities for parsing unified diffs.

Testgap needs two line maps out of a diff:
  - `added_lines`: which new-file lines were ADDED per file — the "changed
    lines" side of the gap intersection (context lines aren't changes).
  - `commentable_lines`: which new-file lines GitHub will accept an inline
    comment on (added + context lines inside hunks) — used by --post, since
    the Pull Request Reviews API rejects comments on lines outside the diff
    (422 "line must be part of the diff").
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

_FILE_HEADER_RE = re.compile(r"^\+\+\+ b/(?P<path>.+)$")
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@")
_DIFF_GIT_RE = re.compile(r'^diff --git (?:"?a/)(?P<a>.+?)"? "?b/(?P<b>.+?)"?$')


@dataclass
class FileDiff:
    """One file's portion of a unified diff, headers included."""

    path: str
    text: str

    def __len__(self) -> int:
        return len(self.text)


def split_diff_by_file(diff_text: str) -> list[FileDiff]:
    """
    Split a unified diff into per-file segments.

    The path is taken from the `+++ b/...` header (new-file path); for
    deletions (`+++ /dev/null`) it falls back to the `diff --git a/...` path,
    so ignore-patterns still match files being removed.
    """
    segments: list[FileDiff] = []
    current_lines: list[str] = []
    current_path = ""
    fallback_path = ""

    def flush() -> None:
        if current_lines:
            segments.append(
                FileDiff(path=current_path or fallback_path, text="\n".join(current_lines))
            )

    for line in diff_text.splitlines():
        git_match = _DIFF_GIT_RE.match(line)
        if git_match:
            flush()
            current_lines = [line]
            current_path = ""
            fallback_path = git_match.group("a")
            continue

        if current_lines or not segments:
            # Content before any "diff --git" (e.g. bare `+++/---` diffs) starts
            # an implicit first segment.
            if not current_lines and not segments:
                current_lines = []
            file_match = _FILE_HEADER_RE.match(line)
            if file_match:
                current_path = file_match.group("path")
            current_lines.append(line)

    flush()
    return [s for s in segments if s.path]


def path_matches(path: str, patterns: list[str]) -> bool:
    """
    Glob matching with `**` support, the way .oneportrc users expect:

      migrations/**        any file under migrations/
      **/*.generated.py    any generated file at any depth
      vendor/**            everything vendored

    fnmatch treats `*` as crossing `/`, which makes `*.py` unexpectedly match
    `a/b.py` — so we translate globs ourselves: `*` and `?` never cross a
    slash, `**` does.
    """
    path = path.replace("\\", "/")
    for pattern in patterns:
        if _glob_to_re(pattern).match(path):
            return True
    return False


def _glob_to_re(pattern: str) -> re.Pattern:
    out = []
    i = 0
    p = pattern.replace("\\", "/")
    while i < len(p):
        c = p[i]
        if c == "*":
            if p[i:i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if p[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _walk_hunk_lines(diff_text: str, want_context: bool) -> dict[str, set[int]]:
    """Shared hunk walker: new-file line numbers per file, added lines always,
    context lines only when want_context is True."""
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
        if raw_line.startswith("+"):
            result[current_file].add(new_line)
            new_line += 1
        elif raw_line.startswith(" "):
            if want_context:
                result[current_file].add(new_line)
            new_line += 1
        elif raw_line.startswith("\\"):
            continue  # "\ No newline at end of file" — doesn't consume a line
        else:
            # Diff header we don't recognise (e.g. "diff --git ..." for the
            # next file) — stop trusting the line counter until the next hunk.
            in_hunk = False

    return dict(result)


def added_lines(diff_text: str) -> dict[str, set[int]]:
    """
    Parse a unified diff and return {file_path: {line_numbers}} for every
    ADDED line (new-file numbering). These are the "changed lines" testgap
    intersects with uncovered lines — deleted lines no longer exist and
    context lines weren't changed.
    """
    return _walk_hunk_lines(diff_text, want_context=False)


def commentable_lines(diff_text: str) -> dict[str, set[int]]:
    """
    Parse a unified diff and return {file_path: {line_numbers}} for every
    new-file line number that appears inside a hunk.

    A line is commentable if it's a "+" (added) or " " (context) line within a
    hunk — those exist in the new file at a known line number. "-" (deleted)
    lines don't exist in the new file and are never commentable.
    """
    return _walk_hunk_lines(diff_text, want_context=True)

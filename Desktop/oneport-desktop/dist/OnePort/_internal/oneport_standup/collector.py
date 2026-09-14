"""
The collector — turns a git range + author filter into structured Commits.

Fully deterministic: one `git log` call, parsed. No model, no network. Ticket keys
(JIRA-style ABC-123) and PR refs (#42) are extracted from commit subjects so the
narrative can group by them. Merge commits are excluded — they're not work.
"""

from __future__ import annotations

import re
from pathlib import Path

from oneport_standup.integrations.local_git import run_git
from oneport_standup.result import Commit

# Record/field separators unlikely to appear in commit metadata.
_REC = "\x1e"
_FIELD = "\x1f"
_FORMAT = _REC + _FIELD.join(["%H", "%an", "%ae", "%aI", "%s"])

# JIRA-style keys (ABC-123) and multi-segment IDs like CVE-2026-35193.
_TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]+(?:-\d+)+)\b")
# PR/issue refs anywhere, including "(#42)" — not just whitespace-preceded.
_PR_RE = re.compile(r"(#\d+)\b")


def collect_commits(
    root: str | Path,
    revrange: str | None = None,
    since: str | None = None,
    author_email: str | None = None,
) -> list[Commit]:
    """
    Collect commits for a range.

    Exactly one of `revrange` (e.g. "v1.0..v1.1") or `since` (e.g. "1 day ago",
    "2026-07-01") drives the window. `author_email` filters to one author when set.
    """
    args = ["log", "--no-merges", f"--format={_FORMAT}", "--numstat"]
    if author_email:
        args.append(f"--author={author_email}")
    if revrange:
        args.append(revrange)
    elif since:
        args.append(f"--since={since}")

    out = run_git(root, *args, timeout=60)
    return _parse_log(out)


def _parse_log(out: str) -> list[Commit]:
    commits: list[Commit] = []
    # Each record starts with _REC; split and drop the empty head.
    for chunk in out.split(_REC):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        lines = chunk.split("\n")
        meta = lines[0].split(_FIELD)
        if len(meta) < 5:
            continue
        sha, an, ae, date, subject = meta[0], meta[1], meta[2], meta[3], meta[4]

        files = insertions = deletions = 0
        for stat in lines[1:]:
            stat = stat.strip()
            if not stat:
                continue
            parts = stat.split("\t")
            if len(parts) != 3:
                continue
            add, rem, _path = parts
            files += 1
            insertions += _num(add)
            deletions += _num(rem)

        commits.append(Commit(
            sha=sha, author=an, email=ae, date=date, subject=subject,
            files=files, insertions=insertions, deletions=deletions,
            tickets=_dedupe(_TICKET_RE.findall(subject)),
            prs=_dedupe(_PR_RE.findall(subject)),
        ))
    return commits


def _num(token: str) -> int:
    # numstat uses "-" for binary files
    return int(token) if token.isdigit() else 0


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for it in items:
        if it not in seen:
            seen.append(it)
    return seen

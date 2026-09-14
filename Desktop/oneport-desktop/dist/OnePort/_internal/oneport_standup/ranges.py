"""
Resolve a mode + flags into a concrete git window and author filter.

Keeps the git-approxidate details in one place so the CLI commands stay thin.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oneport_standup.integrations.local_git import current_email


@dataclass
class Window:
    revrange: str | None       # e.g. "v1.0..v1.1" (mutually exclusive with `since`)
    since: str | None          # git approxidate, e.g. "1 day ago"
    label: str                 # human label for the report header


def window_for(mode: str, value: str | None = None, to_ref: str | None = None) -> Window:
    """
    daily   → last 24h
    weekly  → last 7 days
    since   → `value` as a git --since (date/approxidate) or a `ref..HEAD` range
    release → `value..to_ref` (to_ref defaults to HEAD)
    """
    if mode == "daily":
        return Window(None, "1 day ago", "the last 24 hours")
    if mode == "weekly":
        return Window(None, "7 days ago", "the last 7 days")
    if mode == "release":
        base = value or ""
        head = to_ref or "HEAD"
        return Window(f"{base}..{head}", None, f"{base}..{head}")
    if mode == "since":
        v = value or "1 day ago"
        # A bare git ref (tag/branch/sha) → range from there to HEAD; else a date.
        if _looks_like_ref(v):
            return Window(f"{v}..HEAD", None, f"since {v}")
        return Window(None, v, f"since {v}")
    return Window(None, "1 day ago", "the last 24 hours")


def _looks_like_ref(v: str) -> bool:
    # Dates/approxidates contain spaces, digits-with-dashes, or words like "ago".
    if any(ch.isspace() for ch in v) or "ago" in v.lower():
        return False
    if v[:4].isdigit() and "-" in v:      # 2026-07-01
        return False
    return True


@dataclass
class Author:
    email: str | None          # None = everyone
    label: str


def author_for(root: str | Path, all_authors: bool, override: str | None) -> Author:
    if all_authors:
        return Author(None, "everyone")
    if override:
        return Author(override, override)
    me = current_email(root)
    if me:
        return Author(me, f"you ({me})")
    return Author(None, "everyone")   # no git identity configured — don't filter

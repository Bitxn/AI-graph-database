"""
Result types for oneport-standup.

A `Commit` is one deterministic fact from git. `Narrative` is the LLM's grouping of
those facts into human-readable themes — it never adds work that isn't in the
commits. `StandupReport` bundles both plus the range/author context, and serialises
to JSON for tooling.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    date: str            # ISO 8601
    subject: str
    files: int = 0
    insertions: int = 0
    deletions: int = 0
    tickets: list[str] = field(default_factory=list)   # e.g. ["ABC-123"]
    prs: list[str] = field(default_factory=list)        # e.g. ["#42"]

    @property
    def short(self) -> str:
        return self.sha[:8]

    @property
    def day(self) -> str:
        return self.date[:10]


@dataclass
class Group:
    """One themed bucket in the narrative."""
    title: str
    bullets: list[str] = field(default_factory=list)


@dataclass
class Narrative:
    headline: str = ""
    groups: list[Group] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.headline and not self.groups


@dataclass
class StandupReport:
    mode: str                       # daily | weekly | since | release
    range_label: str                # human label, e.g. "since yesterday"
    author_label: str               # "you (a@b.com)" | "everyone" | "a@b.com"
    commits: list[Commit] = field(default_factory=list)
    narrative: Narrative = field(default_factory=Narrative)
    model: str = ""
    total_tokens: int = 0
    elapsed_ms: int = 0
    notes: list[str] = field(default_factory=list)

    # ── deterministic rollups ────────────────────────────────────────────────
    @property
    def files_touched(self) -> int:
        return sum(c.files for c in self.commits)

    @property
    def net_lines(self) -> tuple[int, int]:
        return (sum(c.insertions for c in self.commits),
                sum(c.deletions for c in self.commits))

    @property
    def tickets(self) -> list[str]:
        seen: list[str] = []
        for c in self.commits:
            for t in c.tickets:
                if t not in seen:
                    seen.append(t)
        return seen

    def by_day(self) -> dict[str, list[Commit]]:
        out: dict[str, list[Commit]] = {}
        for c in self.commits:
            out.setdefault(c.day, []).append(c)
        return out

    def to_dict(self) -> dict[str, Any]:
        ins, dele = self.net_lines
        return {
            "mode": self.mode,
            "range": self.range_label,
            "author": self.author_label,
            "model": self.model,
            "total_tokens": self.total_tokens,
            "elapsed_ms": self.elapsed_ms,
            "stats": {
                "commits": len(self.commits),
                "files_touched": self.files_touched,
                "insertions": ins,
                "deletions": dele,
                "tickets": self.tickets,
            },
            "narrative": {
                "headline": self.narrative.headline,
                "groups": [asdict(g) for g in self.narrative.groups],
            },
            "commits": [asdict(c) for c in self.commits],
            "notes": self.notes,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

"""
Waivers — approve a specific finding without silencing the rule everywhere.

A review gate is only trusted if a team can say "yes, we know about this one,
it's accepted" and have the gate respect that — narrowly, on the record, and
with an expiry — instead of the blunt alternative of ignoring the rule globally
(which also hides the next, real instance of it).

A waiver lives in `.oneport/review-waivers.yml`:

    waivers:
      - file: "src/legacy/*.py"     # glob, or a bare basename
        rule_id: SEC001             # optional — omit to waive every rule in that file
        category: security          # optional — waive a whole category in that file
        reason: "legacy module, scheduled for deletion in Q3 — JIRA-1234"
        expires: "2026-12-31"       # optional — after this date the finding blocks again

A waived issue is still shown (marked ``[WAIVED]``) and still travels into SARIF
— as a ``suppressions`` entry, so GitHub Code Scanning shows it dismissed rather
than dropping it — but it does not count toward the blocking (CI-fail) decision.
An expired waiver is ignored, so the finding silently re-blocks; a stale
approval can't outlive its stated reason.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from oneport.result import Issue

WAIVERS_PATH = ".oneport/review-waivers.yml"


@dataclass(frozen=True)
class Waiver:
    file: str
    reason: str = ""
    rule_id: str = ""
    category: str = ""
    expires: str = ""   # ISO date "YYYY-MM-DD"; empty = never expires

    def is_expired(self, today: date | None = None) -> bool:
        if not self.expires:
            return False
        today = today or date.today()
        try:
            return date.fromisoformat(self.expires) < today
        except ValueError:
            # An unparseable expiry is treated as already expired — fail closed,
            # so a typo can't grant a permanent silent waiver.
            return True

    def matches(self, issue: "Issue") -> bool:
        """True when this waiver covers the given issue.

        File is matched as a glob against the full path OR the basename, so both
        ``src/legacy/db.py`` and ``db.py`` work. rule_id / category, when set,
        must match exactly (case-insensitive for category)."""
        path = issue.file
        if not (fnmatch.fnmatch(path, self.file)
                or fnmatch.fnmatch(os.path.basename(path), self.file)):
            return False
        if self.rule_id and self.rule_id != issue.rule_id:
            return False
        if self.category and self.category.lower() != (issue.category or "").lower():
            return False
        return True


def load_waivers(root: str | Path = ".") -> list[Waiver]:
    """Load waivers from ``<root>/.oneport/review-waivers.yml``.

    A missing or malformed file yields no waivers (never raises) — a broken
    waiver file must not take the gate down, and must not silently pass code."""
    path = Path(root) / WAIVERS_PATH
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    except (yaml.YAMLError, OSError, ValueError):
        return []
    entries = data.get("waivers", []) if isinstance(data, dict) else []
    waivers: list[Waiver] = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("file"):
            continue
        waivers.append(Waiver(
            file=str(e["file"]),
            reason=str(e.get("reason", "")),
            rule_id=str(e.get("rule_id", "")),
            category=str(e.get("category", "")),
            expires=str(e.get("expires", "")),
        ))
    return waivers


def apply_waivers(issues: list["Issue"], waivers: list[Waiver],
                  today: date | None = None) -> int:
    """Mark each issue covered by a non-expired waiver as waived. Returns the count.

    An issue is waived by the FIRST matching, non-expired waiver; its reason is
    recorded on the issue so formatters can show why it was allowed through."""
    active = [w for w in waivers if not w.is_expired(today)]
    count = 0
    for issue in issues:
        for w in active:
            if w.matches(issue):
                issue.waived = True
                issue.waiver_reason = w.reason
                count += 1
                break
    return count

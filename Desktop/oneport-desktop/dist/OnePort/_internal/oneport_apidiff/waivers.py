"""
Waivers — approved, time-boxed exceptions to the gate.

A platform team sometimes ships a break on purpose (consumers already migrated,
an internal-only symbol, a planned major bump). `--allow-breaking` is too blunt
for that: it opens the gate for *everything*. A waiver acknowledges ONE specific
change by symbol (optionally narrowed by change kind), with a reason and an
optional expiry. A waived change still appears in the report — clearly marked —
but no longer fails the build. An expired waiver is ignored, so the gate closes
again automatically instead of hiding a break forever.

File: `.oneport/apidiff-waivers.yml` (repo-relative), format:

    waivers:
      - symbol: "core.Client.fetch"     # exact qualname (or OpenAPI endpoint)
        reason: "consumers migrated in v3"
        expires: "2026-12-31"           # optional ISO date; after it, no longer waives
        kind: "symbol_removed"          # optional — narrow to one change kind
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from oneport_apidiff.result import Change

WAIVERS_PATH = ".oneport/apidiff-waivers.yml"


@dataclass(frozen=True)
class Waiver:
    symbol: str
    reason: str = ""
    expires: str = ""  # ISO date "YYYY-MM-DD", or "" for no expiry
    kind: str = ""  # optional ChangeKind value to narrow the match

    def is_expired(self, today: date) -> bool:
        if not self.expires:
            return False
        try:
            return date.fromisoformat(self.expires) < today
        except ValueError:
            return False  # malformed date: treat as no-expiry rather than silently drop

    def matches(self, change: Change) -> bool:
        if change.symbol != self.symbol:
            return False
        return not self.kind or change.kind.value == self.kind


def load_waivers(repo_root: str | Path, path: str = WAIVERS_PATH) -> list[Waiver]:
    """Load waivers from `<repo_root>/<path>`. Missing file → no waivers.

    Malformed entries are skipped (a waiver file must never crash the gate);
    a completely unparseable file yields no waivers, so the gate stays strict.
    """
    file = Path(repo_root) / path
    if not file.exists():
        return []
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8", errors="replace")) or {}
    except (yaml.YAMLError, OSError, ValueError):
        return []
    raw = data.get("waivers") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []

    waivers: list[Waiver] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("symbol"):
            continue
        waivers.append(
            Waiver(
                symbol=str(item["symbol"]),
                reason=str(item.get("reason", "") or ""),
                expires=str(item.get("expires", "") or ""),
                kind=str(item.get("kind", "") or ""),
            )
        )
    return waivers


def apply_waivers(
    changes: list[Change], waivers: list[Waiver], today: date | None = None
) -> int:
    """Mark changes covered by an unexpired waiver. Returns the count waived."""
    if not waivers:
        return 0
    today = today or date.today()
    active = [w for w in waivers if not w.is_expired(today)]
    waived = 0
    for change in changes:
        for w in active:
            if w.matches(change):
                change.waived = True
                change.waiver_reason = w.reason
                waived += 1
                break
    return waived

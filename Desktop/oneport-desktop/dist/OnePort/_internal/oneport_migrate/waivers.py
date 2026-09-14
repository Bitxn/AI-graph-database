"""
Waivers — approved, time-boxed exceptions to the migration gate.

`rules.ignore` disables a rule everywhere; a waiver is finer: it approves ONE
specific finding — this DROP COLUMN, in this migration file, signed off by the
DBA — while the rule keeps firing on every other migration. The finding stays in
the report, clearly marked, but no longer blocks CI. An expired waiver is
ignored, so a one-off approval can't silently suppress the rule forever.

File: `.oneport/migrate-waivers.yml` (repo-relative):

    waivers:
      - file: "app/migrations/0042_drop_legacy.py"   # glob against the finding's file
        rule_id: "OPM001"                             # optional — narrow to one rule
        reason: "legacy_events unused since v3; DBA signed off in CHG-2211"
        expires: "2026-12-31"                         # optional ISO date
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from oneport_migrate.result import Finding

WAIVERS_PATH = ".oneport/migrate-waivers.yml"


@dataclass(frozen=True)
class Waiver:
    file: str
    rule_id: str = ""
    reason: str = ""
    expires: str = ""

    def is_expired(self, today: date) -> bool:
        if not self.expires:
            return False
        try:
            return date.fromisoformat(self.expires) < today
        except ValueError:
            return False  # malformed date: treat as no-expiry, never silently drop

    def matches(self, finding: Finding) -> bool:
        f = (finding.file or "").replace("\\", "/")
        if not (fnmatch.fnmatch(f, self.file) or fnmatch.fnmatch(Path(f).name, self.file)):
            return False
        return not self.rule_id or finding.rule_id == self.rule_id


def load_waivers(root: str | Path = ".", path: str = WAIVERS_PATH) -> list[Waiver]:
    """Load waivers from `<root>/<path>`. Missing/malformed → no waivers (the gate
    stays strict; a waiver file must never crash or silently open the gate)."""
    file = Path(root) / path
    if not file.exists():
        return []
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8", errors="replace")) or {}
    except (yaml.YAMLError, OSError, ValueError):
        return []
    raw = data.get("waivers") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[Waiver] = []
    for item in raw:
        if isinstance(item, dict) and item.get("file"):
            out.append(Waiver(
                file=str(item["file"]),
                rule_id=str(item.get("rule_id", "") or ""),
                reason=str(item.get("reason", "") or ""),
                expires=str(item.get("expires", "") or ""),
            ))
    return out


def apply_waivers(findings: list[Finding], waivers: list[Waiver], today: date | None = None) -> int:
    """Mark findings covered by an unexpired waiver. Returns the count waived."""
    if not waivers:
        return 0
    today = today or date.today()
    active = [w for w in waivers if not w.is_expired(today)]
    n = 0
    for f in findings:
        for w in active:
            if w.matches(f):
                f.waived = True
                f.waiver_reason = w.reason
                n += 1
                break
    return n

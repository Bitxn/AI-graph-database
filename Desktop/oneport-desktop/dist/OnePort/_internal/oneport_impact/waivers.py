"""
Waivers — approved, time-boxed exceptions to the impact gate.

Some files are legitimately load-bearing: a core client everyone imports, a
settings module a hundred call sites read. Blocking every change to them forever
isn't safety, it's friction. A waiver acknowledges ONE such file (optionally one
rule) with a reason and an optional expiry: the finding still appears in the
report — clearly marked — but no longer fails CI. An expired waiver is ignored,
so the gate closes again automatically instead of hiding risk indefinitely.

File: `.oneport/impact-waivers.yml` (repo-relative):

    waivers:
      - path: "billing/stripe.py"     # glob, matched against the finding's file
        rule_id: "IMP001"             # optional — narrow to one rule
        reason: "core client, reviewed every change in #eng-payments"
        expires: "2026-12-31"         # optional ISO date
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from oneport_impact.result import Finding

WAIVERS_PATH = ".oneport/impact-waivers.yml"


@dataclass(frozen=True)
class Waiver:
    path: str
    rule_id: str = ""
    reason: str = ""
    expires: str = ""

    def is_expired(self, today: date) -> bool:
        if not self.expires:
            return False
        try:
            return date.fromisoformat(self.expires) < today
        except ValueError:
            return False  # malformed date: treat as no-expiry, don't silently drop

    def matches(self, finding: Finding) -> bool:
        f = (finding.file or "").replace("\\", "/")
        if not (fnmatch.fnmatch(f, self.path) or fnmatch.fnmatch(Path(f).name, self.path)):
            return False
        return not self.rule_id or finding.rule_id == self.rule_id


def load_waivers(root: str | Path, path: str = WAIVERS_PATH) -> list[Waiver]:
    """Load waivers from `<root>/<path>`. Missing/malformed → no waivers (gate
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
        if isinstance(item, dict) and item.get("path"):
            out.append(Waiver(
                path=str(item["path"]),
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

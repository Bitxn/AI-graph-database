"""
Waivers — accept a known deprecated/removed-API usage on the record.

A framework-migration scan will flag usages you already know about and have
decided to keep for now: a shim you maintain deliberately, a false positive, or
a removal you'll handle in a later phase (tracked in a ticket). A scanner with no
way to say "yes, we know, leave that one" gets turned off — and then it stops
catching the *next* breaking change. A waiver approves one specific finding,
narrowly, with a reason and an expiry.

A waiver lives in `.oneport/upgrade-waivers.yml`:

    waivers:
      - rule_id: "django.url-to-re_path"   # the rule that fired (glob ok)
        file: "legacy/urls.py"             # optional — restrict to a file (glob)
        reason: "kept intentionally until the legacy app is retired (JIRA-42)"
        expires: "2026-12-31"
      - file: "vendor/**"                  # ...or waive a whole path
        reason: "vendored third-party code — not ours to migrate"

At least one of ``rule_id`` / ``file`` is required. A waived finding is still
shown (marked ``[WAIVED]``) and exported to SARIF as a ``suppressions`` entry,
but it never trips ``--fail-on-removed`` **and is left un-rewritten by ``apply``**
(the point of a waiver is to keep that code as-is). An expired waiver is ignored,
so the finding re-blocks — an approval can't outlive its stated reason.
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
    from oneport_upgrade.result import Finding

WAIVERS_PATH = ".oneport/upgrade-waivers.yml"


@dataclass(frozen=True)
class Waiver:
    rule_id: str = ""       # glob on Finding.rule_id
    file: str = ""          # glob on Finding.file (full path or basename)
    reason: str = ""
    expires: str = ""       # ISO date "YYYY-MM-DD"; empty = never expires

    def is_expired(self, today: date | None = None) -> bool:
        if not self.expires:
            return False
        today = today or date.today()
        try:
            return date.fromisoformat(self.expires) < today
        except ValueError:
            # Unparseable expiry → treat as expired (fail closed) so a typo can't
            # grant a permanent silent waiver.
            return True

    def matches(self, finding: "Finding") -> bool:
        """True when this waiver covers the finding.

        ``rule_id`` matches Finding.rule_id as a glob; ``file`` matches the full
        path OR the basename as a glob. When both are set, both must match."""
        if self.rule_id and not fnmatch.fnmatch(finding.rule_id, self.rule_id):
            return False
        if self.file:
            path = (finding.file or "").replace("\\", "/")
            if not (fnmatch.fnmatch(path, self.file)
                    or fnmatch.fnmatch(os.path.basename(path), self.file)):
                return False
        return True


def load_waivers(root: str | Path = ".") -> list[Waiver]:
    """Load waivers from ``<root>/.oneport/upgrade-waivers.yml``.

    A missing or malformed file yields no waivers (never raises) — a broken
    waiver file must not take the gate down. ``errors="replace"`` so a stray
    non-UTF-8 byte (an accented name saved as latin-1) degrades instead of
    crashing the scan with a UnicodeDecodeError."""
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
        if not isinstance(e, dict) or not (e.get("rule_id") or e.get("file")):
            continue
        waivers.append(Waiver(
            rule_id=str(e.get("rule_id", "")),
            file=str(e.get("file", "")),
            reason=str(e.get("reason", "")),
            expires=str(e.get("expires", "")),
        ))
    return waivers


def apply_waivers(findings: list["Finding"], waivers: list[Waiver],
                  today: date | None = None) -> int:
    """Mark each finding covered by a non-expired waiver as waived. Returns count."""
    active = [w for w in waivers if not w.is_expired(today)]
    count = 0
    for finding in findings:
        for w in active:
            if w.matches(finding):
                finding.waived = True
                finding.waiver_reason = w.reason
                count += 1
                break
    return count

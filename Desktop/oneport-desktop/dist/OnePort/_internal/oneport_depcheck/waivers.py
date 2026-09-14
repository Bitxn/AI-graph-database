"""
Waivers — accept a known advisory on the record, without turning off the gate.

Every security team hits vulnerabilities they've triaged and consciously
accepted: a false positive, an advisory that doesn't apply to how they use the
package, or a real one they can't fix until next quarter (tracked in a ticket).
A scanner with no way to say "yes, we know about CVE-XXXX, it's handled" gets
turned off entirely — and then it stops catching the *next* CVE. A waiver
approves one specific finding, narrowly, with a reason and an expiry.

A waiver lives in `.oneport/depcheck-waivers.yml`:

    waivers:
      - vuln: "CVE-2024-1234"           # CVE / GHSA id (exact or glob), matched
                                        # against the finding's id AND its aliases
        reason: "not reachable — we never call the affected parser (JIRA-123)"
        expires: "2026-06-30"
      - package: "lodash"               # ...or waive by package (glob)
        vuln: "GHSA-*"                  # optionally narrowed to certain advisories
        reason: "pinned by an upstream we don't control; upgrade tracked in OPS-7"

At least one of ``vuln`` / ``package`` is required. A waived finding is still
shown (marked ``[WAIVED]``) and still travels into SARIF as a ``suppressions``
entry, but it never trips ``--fail-on``. An expired waiver is ignored, so the
finding re-blocks — an approval can't outlive its stated reason.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from oneport_depcheck.result import Finding

WAIVERS_PATH = ".oneport/depcheck-waivers.yml"


@dataclass(frozen=True)
class Waiver:
    vuln: str = ""          # CVE/GHSA id glob; matched vs finding.vuln_id + aliases
    package: str = ""       # package-name glob
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

        ``vuln`` matches the finding's OSV id OR any of its aliases (so a CVE id
        waives a GHSA-identified finding and vice-versa) as a glob; ``package``
        matches the package name as a glob. When both are set, both must match."""
        if self.vuln:
            ids = [finding.vuln_id, *finding.aliases]
            if not any(fnmatch.fnmatch(i, self.vuln) for i in ids):
                return False
        if self.package and not fnmatch.fnmatch(finding.package, self.package):
            return False
        return True


def load_waivers(root: str | Path = ".") -> list[Waiver]:
    """Load waivers from ``<root>/.oneport/depcheck-waivers.yml``.

    A missing or malformed file yields no waivers (never raises) — a broken
    waiver file must not take the gate down, and must not silently pass a CVE."""
    path = Path(root) / WAIVERS_PATH
    if not path.exists():
        return []
    try:
        # errors="replace": a waiver file saved as latin-1/cp1252 (an accented
        # name or an em-dash in a reason) must never crash the gate with a
        # UnicodeDecodeError — degrade the stray byte, keep the waivers.
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    except (yaml.YAMLError, OSError, ValueError):
        return []
    entries = data.get("waivers", []) if isinstance(data, dict) else []
    waivers: list[Waiver] = []
    for e in entries:
        if not isinstance(e, dict) or not (e.get("vuln") or e.get("package")):
            continue
        waivers.append(Waiver(
            vuln=str(e.get("vuln", "")),
            package=str(e.get("package", "")),
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

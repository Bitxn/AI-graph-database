"""
Waivers — accept a known coverage gap on the record, without disabling the gate.

Some untested code is untested on purpose: a generated migration, a thin
`__main__` shim, a throwaway script. A coverage gate that can't express "yes, we
know, that one's fine" gets turned off entirely — and then it stops catching the
gaps that do matter. A waiver approves one specific gap, narrowly, with a reason
and an expiry, so the gate keeps working everywhere else.

A waiver lives in `.oneport/testgap-waivers.yml`:

    waivers:
      - file: "**/migrations/*.py"     # glob on the gap's file (path or basename)
        reason: "generated migrations are exercised in staging, not unit-tested"
        expires: "2026-12-31"
      - file: "app/cli.py"
        function: "main"               # optional — restrict to one function
        risk: high                     # optional — only waive at/below this risk
        reason: "entrypoint smoke-tested by the e2e suite"

A waived gap is still shown (marked ``[WAIVED]``) and still travels into SARIF as
a ``suppressions`` entry, but it never counts toward the ``--fail-on`` gate. An
expired waiver is ignored, so the gap re-blocks — an approval can't outlive its
stated reason.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from oneport_testgap.gaps import RISK_ORDER, Risk

if TYPE_CHECKING:
    from oneport_testgap.gaps import Gap

WAIVERS_PATH = ".oneport/testgap-waivers.yml"


@dataclass(frozen=True)
class Waiver:
    file: str
    reason: str = ""
    function: str = ""
    risk: str = ""          # only waive gaps at or BELOW this risk (e.g. "high"
                            # waives high/medium/low but not critical); "" = any
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

    def matches(self, gap: "Gap") -> bool:
        """True when this waiver covers the gap.

        ``file`` matches the gap's path OR basename as a glob; ``function``, when
        set, must match exactly; ``risk``, when set, caps the risk it will waive
        (so a `risk: high` waiver never silently accepts a critical gap)."""
        path = gap.file or ""
        if not (fnmatch.fnmatch(path, self.file)
                or fnmatch.fnmatch(os.path.basename(path), self.file)):
            return False
        if self.function and self.function != gap.function:
            return False
        if self.risk:
            try:
                if RISK_ORDER[gap.risk] < RISK_ORDER[Risk(self.risk)]:
                    return False   # gap is riskier than the waiver allows
            except (KeyError, ValueError):
                return False
        return True


def load_waivers(root: str | Path = ".") -> list[Waiver]:
    """Load waivers from ``<root>/.oneport/testgap-waivers.yml``.

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
            function=str(e.get("function", "")),
            risk=str(e.get("risk", "")),
            expires=str(e.get("expires", "")),
        ))
    return waivers


def apply_waivers(gaps: list["Gap"], waivers: list[Waiver],
                  today: date | None = None) -> int:
    """Mark each gap covered by a non-expired waiver as waived. Returns the count."""
    active = [w for w in waivers if not w.is_expired(today)]
    count = 0
    for gap in gaps:
        for w in active:
            if w.matches(gap):
                gap.waived = True
                gap.waiver_reason = w.reason
                count += 1
                break
    return count

"""
Waivers — accept a specific cost finding on the record, without silencing the rule.

A cost gate is only usable if a team can say "yes, that always-on GPU box / that
NAT gateway is intentional — don't fail our build over it" narrowly, with a
reason and an expiry, instead of the blunt alternative of turning the check off
(which also hides the next, genuinely wasteful instance).

A waiver lives in `.oneport/costwatch-waivers.yml`:

    waivers:
      - resource: "aws_instance.ml_trainer"   # glob on the resource address
        reason: "GPU box is intentionally always-on for the training queue"
        expires: "2026-12-31"
      - file: "envs/dev/*.tf"                  # ...or match by file
        category: dev-no-shutdown
        reason: "dev is torn down nightly by a separate job"

At least one of ``resource`` / ``file`` is required. A waived finding is still
shown (marked ``[WAIVED]``) and still travels into SARIF as a ``suppressions``
entry, but it never contributes to the ``--fail-on`` gate. An expired waiver is
ignored, so the finding re-blocks — an approval can't outlive its stated reason.
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
    from costwatch.result import Finding

WAIVERS_PATH = ".oneport/costwatch-waivers.yml"


@dataclass(frozen=True)
class Waiver:
    resource: str = ""      # glob on Finding.resource, e.g. "aws_instance.*"
    file: str = ""          # glob on Finding.file (full path or basename)
    category: str = ""      # optional — restrict to one waste category
    reason: str = ""
    expires: str = ""       # ISO date "YYYY-MM-DD"; empty = never expires

    def is_expired(self, today: date | None = None) -> bool:
        if not self.expires:
            return False
        today = today or date.today()
        try:
            return date.fromisoformat(self.expires) < today
        except ValueError:
            # An unparseable expiry is treated as expired — fail closed so a
            # typo can't grant a permanent silent waiver.
            return True

    def matches(self, finding: "Finding") -> bool:
        """True when this waiver covers the finding.

        ``resource`` matches Finding.resource as a glob; ``file`` matches the
        full path OR the basename as a glob. When both are set, both must match.
        ``category``, when set, must match exactly (case-insensitive)."""
        if not self.resource and not self.file:
            return False
        if self.resource and not fnmatch.fnmatch(finding.resource, self.resource):
            return False
        if self.file:
            path = finding.file or ""
            if not (fnmatch.fnmatch(path, self.file)
                    or fnmatch.fnmatch(os.path.basename(path), self.file)):
                return False
        if self.category and self.category.lower() != (finding.category or "").lower():
            return False
        return True


def load_waivers(root: str | Path = ".") -> list[Waiver]:
    """Load waivers from ``<root>/.oneport/costwatch-waivers.yml``.

    A missing or malformed file yields no waivers (never raises) — a broken
    waiver file must not take the gate down, and must not silently pass cost."""
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
        if not isinstance(e, dict) or not (e.get("resource") or e.get("file")):
            continue
        waivers.append(Waiver(
            resource=str(e.get("resource", "")),
            file=str(e.get("file", "")),
            category=str(e.get("category", "")),
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

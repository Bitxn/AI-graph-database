"""
Waivers — accept a known, reviewed deviation so it stops blocking.

A waiver is an explicit, auditable decision ("yes, R3 is broken here, and that's
intended for now") — never a silent suppression. Waived deviations still appear
in output, clearly marked, so 'clean' never hides a waiver.

Format: JSON at `.oneport/conformance-waivers.json` (zero extra deps):

    [
      {"rule": "R3", "file": "legacy/pay.py", "reason": "grandfathered", "expires": "2026-12-31"}
    ]

`file` is optional (omit to waive the rule everywhere). `expires` is optional
(ISO date; an expired waiver is ignored, so debt resurfaces on its own).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from oneport_conformance.result import Deviation

DEFAULT_WAIVER_PATHS = (
    ".oneport/conformance-waivers.json",
    ".oneport/conformance_waivers.json",
)


def load_waivers(path: str | Path | None, repo: str | Path | None = None) -> list[dict]:
    """Load waiver entries. Returns [] if none. UTF-8 read on purpose — a BOM or
    non-Latin-1 byte in a reason string must not crash the reader on Windows."""
    candidates = []
    if path:
        candidates.append(Path(path))
    else:
        root = Path(repo or Path.cwd())
        candidates.extend(root / rel for rel in DEFAULT_WAIVER_PATHS)

    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                return []
            return data if isinstance(data, list) else []
    return []


def _active(entry: dict) -> bool:
    exp = entry.get("expires")
    if not exp:
        return True
    try:
        return date.today() <= date.fromisoformat(str(exp))
    except ValueError:
        return True  # unparseable expiry → treat as non-expiring, don't silently drop


def apply_waivers(deviations: list[Deviation], waivers: list[dict]) -> None:
    """Mark deviations as waived in place when a matching, unexpired waiver exists."""
    active = [w for w in waivers if _active(w)]
    for dev in deviations:
        for w in active:
            if w.get("rule") and w["rule"] != dev.rule_id:
                continue
            wf = (w.get("file") or "").strip()
            if wf and wf not in (dev.file or ""):
                continue
            dev.waived = True
            break

"""Local status-history store — customer-owned state for trend detection.

Persisted as a plain JSON file the customer controls (default
`.oneport-apiwatch-history.json`, or read from a prior CI run's artifact). This
is the *only* state the tool keeps, and it never leaves the customer's machine —
that's the serverless moat. The AI layer reads recent history to spot trends like
"latency climbing over the last 3 runs".

File shape::

    {
      "version": 1,
      "checks": {
        "homepage": [
          {"timestamp": "...", "ok": true,  "status_code": 200, "latency_ms": 120.0},
          {"timestamp": "...", "ok": false, "status_code": 500, "latency_ms": 900.0}
        ]
      }
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oneport_apiwatch.result import Report

# Keep this many runs per check. Enough for trend detection, small enough to
# commit as a CI artifact without noise.
MAX_RUNS_PER_CHECK = 20


class History:
    """Read/append the local status-history file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "checks": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt history file must never break monitoring — start fresh.
            return {"version": 1, "checks": {}}
        if not isinstance(data, dict) or "checks" not in data:
            return {"version": 1, "checks": {}}
        return data

    def recent(self, name: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return up to `limit` most-recent prior runs for a check (oldest first)."""
        runs = self._data.get("checks", {}).get(name, [])
        return runs[-limit:]

    def record(self, report: Report) -> None:
        """Append this run's outcomes to each check's history (in memory)."""
        checks = self._data.setdefault("checks", {})
        for result in report.checks:
            runs = checks.setdefault(result.name, [])
            runs.append(
                {
                    "timestamp": report.generated_at,
                    "ok": result.ok,
                    "status_code": result.status_code,
                    "latency_ms": result.latency_ms,
                }
            )
            # Trim to the newest MAX_RUNS_PER_CHECK.
            del runs[:-MAX_RUNS_PER_CHECK]

    def save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

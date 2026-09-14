"""Machine-readable JSON output."""

from __future__ import annotations

from oneport_standup.result import StandupReport


def format_json(report: StandupReport) -> str:
    return report.to_json(indent=2)

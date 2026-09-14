"""Machine-readable JSON output."""

from __future__ import annotations

from oneport_upgrade.result import UpgradeReport


def format_json(report: UpgradeReport) -> str:
    return report.to_json(indent=2)

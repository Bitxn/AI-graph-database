"""Machine-readable JSON — the shape `op ship` normalises and CI consumes."""

from __future__ import annotations

from oneport_impact.result import ImpactReport


def format_json(report: ImpactReport) -> str:
    return report.to_json(indent=2)

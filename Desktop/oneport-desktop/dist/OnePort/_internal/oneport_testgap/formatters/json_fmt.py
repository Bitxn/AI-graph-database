"""
JSON formatter — machine-readable output for CI pipelines and other tools.
"""

from __future__ import annotations

import json

from oneport_testgap.gaps import GapReport


def format_json(report: GapReport, min_risk: str = "low") -> str:
    """Full report as JSON. Nothing is dropped by `min_risk` — every gap is
    emitted with a `shown` flag so consumers can apply or ignore the filter."""
    shown_set = {(g.file, g.function, g.first_line) for g in report.gaps_at_least(min_risk)}

    data = report.to_dict()
    data["min_risk"] = min_risk
    data["has_critical_gaps"] = report.has_critical_gaps
    for gap, gap_dict in zip(report.gaps, data["gaps"]):
        gap_dict["shown"] = (gap.file, gap.function, gap.first_line) in shown_set
    return json.dumps(data, indent=2)

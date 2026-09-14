"""
SARIF 2.1.0 formatter.

A testgap finding is a located code finding — "billing/charge.py:44-71 pay() is
changed but 0% covered, risk HIGH" — so it maps cleanly onto SARIF and can
surface in GitHub Code Scanning or any SARIF viewer alongside lint and security
results. The risk rationale and the code snippet ride along in the message.

Spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""

from __future__ import annotations

import json

from oneport_testgap import __version__
from oneport_testgap.gaps import GapReport, Gap, Risk

_RISK_TO_SARIF = {
    Risk.CRITICAL: "error",
    Risk.HIGH:     "error",
    Risk.MEDIUM:   "warning",
    Risk.LOW:      "note",
    Risk.UNRANKED: "note",
}


def format_sarif(report: GapReport, min_risk: str = "low") -> str:
    """Serialise the gap report to SARIF 2.1.0. ``min_risk`` is accepted for a
    uniform formatter signature but ignored — SARIF emits the full finding set
    (viewers do their own filtering), and waived gaps are marked suppressed."""
    results = [_result(g) for g in report.gaps]
    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "oneport-testgap",
                        "version": __version__,
                        "informationUri": "https://oneport.dev",
                        "rules": _rules(report),
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)


def _rules(report: GapReport) -> list[dict]:
    seen: set[str] = set()
    rules = []
    for g in report.gaps:
        rid = f"uncovered-{g.risk.value}"
        if rid in seen:
            continue
        seen.add(rid)
        rules.append({
            "id": rid,
            "name": rid,
            "shortDescription": {"text": f"Changed code with no test coverage ({g.risk.value} risk)"},
            "helpUri": "https://docs.oneport.dev/testgap/uncovered-changed-code",
        })
    return rules


def _result(gap: Gap) -> dict:
    message = (
        f"{gap.function}: changed lines {gap.line_ranges} have no test coverage."
        + (f"\n\nWhy it matters: {gap.why}" if gap.why else "")
        + (f"\n\n{gap.snippet}" if gap.snippet else "")
    )
    result = {
        "ruleId": f"uncovered-{gap.risk.value}",
        "level": _RISK_TO_SARIF[gap.risk],
        "message": {"text": message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": gap.file},
                    "region": {
                        "startLine": gap.first_line or 1,
                        "endLine": gap.last_line or gap.first_line or 1,
                    },
                }
            }
        ],
        "properties": {
            "risk": gap.risk.value,
            "measured": gap.measured,
        },
    }
    if gap.waived:
        result["suppressions"] = [{
            "kind": "external",
            "justification": gap.waiver_reason or "waived via .oneport/testgap-waivers.yml",
        }]
    return result

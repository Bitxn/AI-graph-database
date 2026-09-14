"""
SARIF 2.1.0 output — for GitHub code scanning and enterprise dashboards.

Emits the gate findings (wide blast radius, unaddressed co-change companions) as
SARIF results so a platform team can route impact into the same place as every
other scanner. Waived findings are emitted as *suppressed*, so the dashboard
shows them acknowledged rather than open.
"""
from __future__ import annotations

import json

from oneport_impact import __version__
from oneport_impact.result import Finding, ImpactReport, Severity

_LEVEL = {
    Severity.CRITICAL: "error",
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "note",
}

_RULES = {
    "IMP001": "Wide blast radius — many call sites depend on this file.",
    "IMP010": "Co-change companion not updated in this change.",
}


def _result(f: Finding) -> dict:
    obj: dict = {
        "ruleId": f.rule_id or "impact",
        "level": _LEVEL.get(f.severity, "warning"),
        "message": {"text": f.message + (f"  Suggested: {f.fix}" if f.fix else "")},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": f.file or ""},
                    "region": {"startLine": max(1, f.line)},
                }
            }
        ],
        "properties": {"severity": f.severity.value, "category": f.category},
    }
    if f.waived:
        obj["suppressions"] = [
            {"kind": "external",
             "justification": f.waiver_reason or "waived via .oneport/impact-waivers.yml"}
        ]
    return obj


def format_sarif(report: ImpactReport) -> str:
    seen: dict[str, dict] = {}
    for f in report.findings:
        rid = f.rule_id or "impact"
        if rid not in seen:
            seen[rid] = {"id": rid, "name": rid,
                         "shortDescription": {"text": _RULES.get(rid, "Impact finding.")}}
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "oneport-impact",
                        "version": __version__,
                        "informationUri": "https://oneport.dev",
                        "rules": list(seen.values()),
                    }
                },
                "results": [_result(f) for f in report.findings],
            }
        ],
    }
    return json.dumps(doc, indent=2)

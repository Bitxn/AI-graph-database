"""
SARIF 2.1.0 formatter.

An upgrade finding is a located finding — "uses removed API `django.conf.urls.url`
at legacy/urls.py:12" — so it maps cleanly onto SARIF and can surface in GitHub
Code Scanning alongside lint and security results. Removed APIs are `error`
(they break on the target runtime); deprecated APIs are `warning`. The suggested
replacement and hint ride along in the message and properties.

Spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""
from __future__ import annotations

import json

from oneport_upgrade import __version__
from oneport_upgrade.result import Finding, UpgradeReport
from oneport_upgrade.rules.schema import REMOVED


def format_sarif(report: UpgradeReport) -> str:
    """Serialise the report's findings to SARIF 2.1.0."""
    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "oneport-upgrade",
                        "version": __version__,
                        "informationUri": "https://oneport.dev",
                        "rules": _rules(report),
                    }
                },
                "results": [_result(f) for f in report.findings],
            }
        ],
    }
    return json.dumps(sarif, indent=2)


def _rules(report: UpgradeReport) -> list[dict]:
    seen: set[str] = set()
    rules = []
    for f in report.findings:
        if f.rule_id in seen:
            continue
        seen.add(f.rule_id)
        rules.append({
            "id": f.rule_id,
            "name": f.rule_id,
            "shortDescription": {"text": f.title},
            "helpUri": f"https://docs.oneport.dev/upgrade/{report.migration}#{f.rule_id}",
            "properties": {"severity": f.severity},
        })
    return rules


def _result(f: Finding) -> dict:
    message = f"{f.title}"
    if f.replacement:
        message += f"\n\nReplace with: {f.replacement}"
    if f.hint:
        message += f"\n\n{f.hint}"
    result = {
        "ruleId": f.rule_id,
        # removed → error (hard break on the target runtime); deprecated → warning.
        "level": "error" if f.severity == REMOVED else "warning",
        "message": {"text": message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": f.file},
                    "region": {"startLine": f.line or 1, "startColumn": (f.col or 0) + 1},
                }
            }
        ],
        "properties": {
            "severity": f.severity,          # removed | deprecated
            "autoFixable": f.auto,
            "replacement": f.replacement,
        },
    }
    if f.waived:
        result["suppressions"] = [{
            "kind": "external",
            "justification": f.waiver_reason or "waived via .oneport/upgrade-waivers.yml",
        }]
    return result

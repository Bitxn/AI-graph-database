"""SARIF 2.1.0 formatter — for GitHub code scanning and quality dashboards.

Migration findings are file:line + rule_id + severity, so they map cleanly onto
SARIF results. Critical/error become `error`, warning `warning`, info `note`.
A waived finding is emitted as *suppressed*, so a dashboard shows it acknowledged
rather than open.
"""

from __future__ import annotations

import json

from oneport_migrate import __version__
from oneport_migrate.result import CheckResult, Finding, Severity

_LEVEL = {
    Severity.CRITICAL: "error",
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "note",
}


def _result(f: Finding) -> dict:
    text = f.message
    if f.suggestion:
        text += f"  Suggested fix: {f.suggestion}"
    if f.blast_radius:
        text += f"  Blast radius: {f.blast_radius}"
    obj: dict = {
        "ruleId": f.rule_id,
        "level": _LEVEL.get(f.severity, "warning"),
        "message": {"text": text},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": f.file},
                    "region": {"startLine": max(1, f.line)},
                }
            }
        ],
        "properties": {"severity": f.severity.value, "category": f.category,
                       "framework": f.framework},
    }
    if f.waived:
        obj["suppressions"] = [
            {"kind": "external",
             "justification": f.waiver_reason or "waived via .oneport/migrate-waivers.yml"}
        ]
    return obj


def _rules(result: CheckResult) -> list[dict]:
    seen: dict[str, dict] = {}
    for f in result.findings:
        if f.rule_id not in seen:
            rule: dict = {"id": f.rule_id, "name": f.rule_id,
                          "shortDescription": {"text": f.message[:120]}}
            if f.docs_url:
                rule["helpUri"] = f.docs_url
            seen[f.rule_id] = rule
    return list(seen.values())


class SarifFormatter:
    def format(self, result: CheckResult) -> str:
        doc = {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "oneport-migrate",
                            "version": __version__,
                            "informationUri": "https://oneport.dev",
                            "rules": _rules(result),
                        }
                    },
                    "results": [_result(f) for f in result.findings],
                }
            ],
        }
        return json.dumps(doc, indent=2)

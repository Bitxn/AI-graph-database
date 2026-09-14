"""
SARIF 2.1.0 formatter.

Costwatch findings are located code findings — "aws_instance.api is oversized at
main.tf:42" — so they map cleanly onto SARIF and can surface in GitHub Code
Scanning, the VS Code Problems panel, or any SARIF-aware tool alongside security
and lint results. The dollar figures the reviewer actually cares about ride
along in result/rule ``properties``.

Spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""

from __future__ import annotations

import json

from costwatch import __version__
from costwatch.formatters.base import Formatter
from costwatch.result import CostReport, Finding, Severity

_SEVERITY_TO_SARIF = {
    Severity.CRITICAL: "error",
    Severity.HIGH:     "error",
    Severity.WARNING:  "warning",
    Severity.INFO:     "note",
}


class SarifFormatter(Formatter):
    """Serialise a CostReport's findings to SARIF 2.1.0 JSON."""

    def format(self, report: CostReport) -> str:
        sarif = {
            "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "oneport-costwatch",
                            "version": __version__,
                            "informationUri": "https://oneport.dev",
                            "rules": self._rules(report),
                        }
                    },
                    "results": [self._result(f) for f in report.findings],
                }
            ],
        }
        return json.dumps(sarif, indent=2)

    def _rules(self, report: CostReport) -> list[dict]:
        seen: set[str] = set()
        rules = []
        for f in report.findings:
            if f.category in seen:
                continue
            seen.add(f.category)
            rules.append({
                "id": f.category,
                "name": f.category,
                "shortDescription": {"text": f"Cost waste: {f.category}"},
                "helpUri": f"https://docs.oneport.dev/costwatch/{f.category}",
            })
        return rules

    def _result(self, f: Finding) -> dict:
        message = (
            f"{f.message}\n\n"
            f"Change: {f.current_config} → {f.suggested_config}\n"
            f"Estimated saving: ~${f.estimated_monthly_saving:,.0f}/mo"
        )
        result = {
            "ruleId": f.category,
            "level": _SEVERITY_TO_SARIF[f.severity],
            "message": {"text": message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.file or f.resource},
                        "region": {"startLine": f.line or 1},
                    }
                }
            ],
            "properties": {
                "resource": f.resource,
                "estimatedMonthlySavingUsd": round(f.estimated_monthly_saving, 2),
                "currentConfig": f.current_config,
                "suggestedConfig": f.suggested_config,
            },
        }
        # A waived finding is reported but marked suppressed, so viewers show it
        # dismissed rather than open.
        if f.waived:
            result["suppressions"] = [{
                "kind": "external",
                "justification": f.waiver_reason or "waived via .oneport/costwatch-waivers.yml",
            }]
        return result

"""
SARIF 2.1.0 formatter.

SARIF is the format consumed by:
  - VS Code Problems panel (via the SARIF Viewer extension or native support)
  - GitHub Code Scanning
  - Azure DevOps
  - Any SAST toolchain that understands the standard

Spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""

from __future__ import annotations

import json

from oneport import __version__
from oneport.formatters.base import Formatter
from oneport.result import ReviewResult, Issue, Severity

_SEVERITY_TO_SARIF = {
    Severity.CRITICAL: "error",
    Severity.ERROR:    "error",
    Severity.WARNING:  "warning",
    Severity.INFO:     "note",
}


class SarifFormatter(Formatter):
    """Serialises ReviewResult to SARIF 2.1.0 JSON."""

    def format(self, result: ReviewResult) -> str:
        rules = self._build_rules(result)
        results = [self._build_result(i) for i in result.issues]

        sarif = {
            "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "oneport-review",
                            "version": __version__,
                            "informationUri": "https://oneport.dev",
                            "rules": rules,
                        }
                    },
                    "results": results,
                }
            ],
        }
        return json.dumps(sarif, indent=2)

    def _build_rules(self, result: ReviewResult) -> list[dict]:
        seen: set[str] = set()
        rules = []
        for issue in result.issues:
            if issue.rule_id in seen:
                continue
            seen.add(issue.rule_id)
            rules.append({
                "id": issue.rule_id,
                "name": issue.rule_id,
                "shortDescription": {"text": issue.message},
                "helpUri": issue.docs_url or f"https://docs.oneport.dev/rules/{issue.rule_id}",
                "properties": {"category": issue.category},
            })
        return rules

    def _build_result(self, issue: Issue) -> dict:
        result = {
            "ruleId": issue.rule_id,
            "level": _SEVERITY_TO_SARIF[issue.severity],
            "message": {
                "text": f"{issue.message}\n\nSuggestion: {issue.suggestion}"
            },
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": issue.file},
                        "region": {
                            "startLine": issue.line,
                            "startColumn": issue.location.column or 1,
                            "endLine": issue.location.end_line or issue.line,
                        },
                    }
                }
            ],
        }
        # A waived finding is reported but marked suppressed, so GitHub Code
        # Scanning (and any SARIF viewer) shows it dismissed rather than open.
        if getattr(issue, "waived", False):
            result["suppressions"] = [{
                "kind": "external",
                "justification": issue.waiver_reason or "waived via .oneport/review-waivers.yml",
            }]
        return result

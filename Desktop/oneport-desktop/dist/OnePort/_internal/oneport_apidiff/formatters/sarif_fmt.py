"""
SARIF 2.1.0 formatter.

SARIF is the format enterprises ingest into GitHub code scanning, Azure DevOps,
and quality dashboards. Emitting it lets a platform team route apidiff findings
into the same place as every other scanner — with breaking changes as `error`,
risky as `warning`, compatible as `note`, and waived changes marked as
`suppressed` so the dashboard shows them acknowledged rather than open.
"""

from __future__ import annotations

import json

from oneport_apidiff import __version__
from oneport_apidiff.result import ApiDiffResult, Change, Verdict

_LEVEL = {
    Verdict.BREAKING: "error",
    Verdict.RISKY: "warning",
    Verdict.COMPATIBLE: "note",
}


def _message(change: Change) -> str:
    parts = [change.detail]
    if change.impact:
        parts.append(f"Impact: {change.impact}")
    if change.migration:
        parts.append(f"Migration: {change.migration}")
    return " ".join(parts)


def _result_obj(change: Change) -> dict:
    obj: dict = {
        "ruleId": change.kind.value,
        "level": _LEVEL.get(change.verdict, "warning"),
        "message": {"text": _message(change)},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": change.file},
                    "region": {"startLine": max(1, change.line)},
                }
            }
        ],
        "properties": {"verdict": change.verdict.value, "symbol": change.symbol},
    }
    if change.waived:
        # GitHub/SARIF renders a suppressed result as acknowledged, not open.
        obj["suppressions"] = [
            {
                "kind": "external",
                "justification": change.waiver_reason or "waived via .oneport/apidiff-waivers.yml",
            }
        ]
    return obj


def _rules(result: ApiDiffResult) -> list[dict]:
    seen: dict[str, dict] = {}
    for change in result.changes:
        rid = change.kind.value
        if rid not in seen:
            seen[rid] = {
                "id": rid,
                "name": rid,
                "shortDescription": {"text": rid.replace("_", " ")},
            }
    return list(seen.values())


def format_sarif(result: ApiDiffResult) -> str:
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "oneport-apidiff",
                        "version": __version__,
                        "informationUri": "https://oneport.dev",
                        "rules": _rules(result),
                    }
                },
                "results": [_result_obj(c) for c in result.changes],
            }
        ],
    }
    return json.dumps(doc, indent=2)

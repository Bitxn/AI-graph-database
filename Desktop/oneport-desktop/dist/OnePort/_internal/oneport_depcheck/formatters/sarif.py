"""
SARIF 2.1.0 formatter.

A depcheck finding is a located finding — "requests==2.32.0 has CVE-2024-XXXX
(CVSS 8.1), reachable, at requirements.txt:14" — so it maps cleanly onto SARIF
and can surface in GitHub Code Scanning alongside SAST and secret results. The
numbers a security reviewer acts on (CVSS, reachability, the fixed version) ride
along in result/rule properties.

Spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""

from __future__ import annotations

import json

from oneport_depcheck import __version__
from oneport_depcheck.result import Finding, ScanResult, Severity

_SEVERITY_TO_SARIF = {
    Severity.CRITICAL: "error",
    Severity.HIGH:     "error",
    Severity.MEDIUM:   "warning",
    Severity.LOW:      "note",
    Severity.UNKNOWN:  "warning",   # unknown severity: warn, don't hide
}


def format_sarif(result: ScanResult) -> str:
    """Serialise the scan's vulnerability findings to SARIF 2.1.0."""
    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "oneport-depcheck",
                        "version": __version__,
                        "informationUri": "https://oneport.dev",
                        "rules": _rules(result),
                    }
                },
                "results": [_result(f) for f in result.findings],
            }
        ],
    }
    return json.dumps(sarif, indent=2)


def _rules(result: ScanResult) -> list[dict]:
    seen: set[str] = set()
    rules = []
    for f in result.findings:
        if f.vuln_id in seen:
            continue
        seen.add(f.vuln_id)
        rules.append({
            "id": f.vuln_id,
            "name": f.cve,
            "shortDescription": {"text": f.summary or f.vuln_id},
            "helpUri": (f.references[0] if f.references
                        else f"https://osv.dev/vulnerability/{f.vuln_id}"),
            "properties": {"cve": f.cve},
        })
    return rules


def _result(f: Finding) -> dict:
    fix = f"\n\nFix: upgrade to {f.upgrade_target}" if f.upgrade_target else (
        f"\n\nFixed in: {f.fixed_version}" if f.fixed_version else "")
    message = (
        f"{f.spec} — {f.summary or f.vuln_id} ({f.cve})\n\n"
        f"Severity: {f.severity.value}"
        + (f" (CVSS {f.cvss_score})" if f.cvss_score is not None else "")
        + f"\nReachability: {f.reachability.value}"
        + (f" — {f.reachability_reason}" if f.reachability_reason else "")
        + fix
    )
    result = {
        "ruleId": f.vuln_id,
        "level": _SEVERITY_TO_SARIF[f.severity],
        "message": {"text": message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": f.manifest},
                    "region": {"startLine": f.manifest_line or 1},
                }
            }
        ],
        "properties": {
            "package": f.package,
            "version": f.version,
            "ecosystem": f.ecosystem,
            "cve": f.cve,
            "cvssScore": f.cvss_score,
            "severity": f.severity.value,
            "reachability": f.reachability.value,
            "fixedVersion": f.fixed_version,
            "isDev": f.is_dev,
        },
    }
    if f.waived:
        result["suppressions"] = [{
            "kind": "external",
            "justification": f.waiver_reason or "waived via .oneport/depcheck-waivers.yml",
        }]
    return result

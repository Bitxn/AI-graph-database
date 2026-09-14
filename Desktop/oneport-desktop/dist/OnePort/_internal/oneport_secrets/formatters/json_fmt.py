"""
Machine-readable JSON output for CI pipelines. Secrets are redacted; a stable
`fingerprint` (hash of the secret value) is included so pipelines can suppress a
known finding without the plaintext ever leaving the machine.
"""

from __future__ import annotations

import json

from oneport_secrets.result import EnvResult, ScanResult


def format_scan(result: ScanResult) -> str:
    return json.dumps({
        "target": result.target,
        "mode": result.mode,
        "scanned_files": result.scanned_files,
        "scanned_commits": result.scanned_commits,
        "triaged": result.triaged,
        "triage_error": result.triage_error,
        "model": result.model,
        "counts": {
            "total": len(result.findings),
            "real": len(result.real),
            "false_positive": len(result.false_positives),
            "blocking": len(result.blocking),
        },
        "findings": [
            {
                "detector_id": f.detector_id,
                "detector_name": f.detector_name,
                "severity": f.severity,
                "verdict": f.verdict.value,
                "reason": f.reason,
                "path": f.path,
                "line": f.line,
                "commit": f.commit,
                "author": f.author,
                "date": f.date,
                "fingerprint": f.fingerprint,
                "redacted": f.redacted(),
                "entropy": f.entropy,
                "remediation": f.remediation,
            }
            for f in result.sorted_findings()
        ],
    }, indent=2)


def format_env(result: EnvResult) -> str:
    return json.dumps({
        "example_path": result.example_path,
        "has_drift": result.has_drift,
        "classified": result.classified,
        "used_but_undeclared": [
            {"name": v.name, "kind": v.kind, "used_in": v.used_in}
            for v in result.used_but_undeclared
        ],
        "declared_but_unused": [
            {"name": v.name, "kind": v.kind} for v in result.declared_but_unused
        ],
    }, indent=2)

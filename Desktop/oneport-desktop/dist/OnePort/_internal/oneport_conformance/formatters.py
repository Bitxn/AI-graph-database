"""Render a ConformanceResult as human text, JSON, or SARIF."""

from __future__ import annotations

import json

from oneport_conformance.result import ConformanceResult

_SEV_LABEL = {"high": "HIGH", "medium": "MED ", "low": "LOW "}


def to_human(result: ConformanceResult, fail_on: str = "medium") -> str:
    lines: list[str] = []
    if result.skipped:
        return f"— conformance skipped: {result.reason}"

    active = result.active()
    blocking = result.blocking(fail_on)
    waived = [d for d in result.deviations if d.waived]

    if not active:
        head = "CONFORMS — no intent deviations found in this change"
    elif blocking:
        head = f"DEVIATES — {len(blocking)} deviation(s) at/above '{fail_on}'"
    else:
        head = f"CONFORMS (with {len(active)} sub-'{fail_on}' note(s))"
    lines.append(head)
    lines.append(f"  checked {result.rules_checked} intent rule(s) · model "
                 f"{result.model} · {result.tokens_used} tokens")
    lines.append("")

    for d in sorted(active, key=lambda x: (-x.sev_rank(), x.rule_id)):
        loc = f" {d.file}:{d.line}" if d.file else ""
        lines.append(f"  [{_SEV_LABEL.get(d.severity, d.severity)}] "
                     f"{d.rule_id} ({d.confidence:.0%} conf){loc}")
        lines.append(f"      rule:     {d.rule_text}")
        if d.observed:
            lines.append(f"      observed: {d.observed}")
        if d.explanation:
            lines.append(f"      why:      {d.explanation}")
        lines.append("")

    for d in waived:
        lines.append(f"  [WAIVED] {d.rule_id} {d.file} — {d.rule_text}")
    if waived:
        lines.append("")

    for n in result.notes:
        lines.append(f"  note: {n}")

    return "\n".join(lines).rstrip()


def to_json(result: ConformanceResult, fail_on: str = "medium") -> str:
    payload = {
        "conforms": result.conforms(fail_on),
        "skipped": result.skipped,
        "reason": result.reason,
        "fail_on": fail_on,
        "rules_checked": result.rules_checked,
        "overall_confidence": round(result.overall_confidence, 3),
        "model": result.model,
        "tokens_used": result.tokens_used,
        "summary": {
            "total": len(result.active()),
            "blocking": len(result.blocking(fail_on)),
            "waived": sum(1 for d in result.deviations if d.waived),
        },
        "deviations": [
            {
                "rule_id": d.rule_id, "rule_text": d.rule_text,
                "observed": d.observed, "severity": d.severity,
                "confidence": round(d.confidence, 3), "explanation": d.explanation,
                "file": d.file, "line": d.line, "waived": d.waived,
            }
            for d in result.deviations
        ],
        "notes": result.notes,
    }
    return json.dumps(payload, indent=2)


def to_sarif(result: ConformanceResult, fail_on: str = "medium") -> str:
    sarif_level = {"high": "error", "medium": "warning", "low": "note"}
    results = []
    for d in result.active():
        results.append({
            "ruleId": f"conformance/{d.rule_id}",
            "level": sarif_level.get(d.severity, "warning"),
            "message": {"text": f"{d.rule_text} — {d.explanation or d.observed}"
                                f" (confidence {d.confidence:.0%})"},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": d.file or "unknown"},
                    "region": {"startLine": d.line or 1},
                }
            }],
        })
    doc = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "oneport-conformance",
                                "informationUri": "https://github.com/oneport/oneport-conformance",
                                "rules": []}},
            "results": results,
        }],
    }
    return json.dumps(doc, indent=2)

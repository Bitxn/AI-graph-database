"""
GitHub formatter — inline PR comments on the exact manifest lines plus a
sticky summary table with a hidden marker for in-place re-run updates.
"""

from __future__ import annotations

from oneport_depcheck.diff_utils import commentable_lines
from oneport_depcheck.markers import SCANNED_MARKER_TPL, STICKY_MARKER
from oneport_depcheck.result import Finding, Reachability, ScanResult

_REACH_EMOJI = {
    Reachability.REACHABLE: "🔴",
    Reachability.UNTRIAGED: "🟠",
    Reachability.LIKELY_UNREACHABLE: "🟡",
    Reachability.DEV_ONLY: "⚪",
}


def build_pr_review(result: ScanResult, diff_text: str) -> dict:
    """Payload for GitHubIntegration.post_review().

    Findings whose manifest line is part of the PR diff become inline comments
    (with a committable ```suggestion when the fix is a plain pin bump).
    Everything else lands in the review body — nothing is silently dropped.

    Returns {"body": str, "comments": [...], "event": "COMMENT"}.
    """
    diff_lines = commentable_lines(diff_text)

    inline: list[Finding] = []
    overflow: list[Finding] = []
    for f in result.findings:
        if f.manifest_line in diff_lines.get(f.manifest, set()):
            inline.append(f)
        else:
            overflow.append(f)

    comments = [_inline_comment(f) for f in inline]
    body = _summary_body(result, inline_count=len(inline), overflow=overflow)

    return {"body": body, "comments": comments, "event": "COMMENT"}


def build_sticky_summary(result: ScanResult, head_sha: str = "") -> str:
    """Body of the sticky summary comment (marker included)."""
    body = _summary_body(result, inline_count=0, overflow=result.findings)
    body += f"\n\n{STICKY_MARKER}"
    if head_sha:
        body += "\n" + SCANNED_MARKER_TPL.format(sha=head_sha)
    return body


def _inline_comment(f: Finding) -> dict:
    emoji = _REACH_EMOJI[f.reachability]
    parts = [
        f"{emoji} **{f.cve}** — `{f.spec}` "
        f"({f.severity.value}, {f.reachability.value})",
        "",
        f.summary or f.vuln_id,
    ]
    if f.reachability_reason:
        parts += ["", f"**Triage:** {f.reachability_reason}"]
    if f.upgrade_target:
        parts += ["", f"**Fix:** upgrade to `{f.upgrade_target}`. {f.break_risk}"]

    # Committable suggestion only when it's an actual replacement line for the
    # manifest (not a shell command for regenerating a lockfile).
    if f.fix_suggestion and _is_replacement_line(f):
        parts += ["", f"```suggestion\n{f.fix_suggestion}\n```"]
    elif f.fix_suggestion:
        parts += ["", f"Run: `{f.fix_suggestion}`"]

    return {"path": f.manifest, "line": f.manifest_line, "body": "\n".join(parts)}


def _is_replacement_line(f: Finding) -> bool:
    manifest = f.manifest.rsplit("/", 1)[-1]
    return manifest.endswith(".txt") or manifest == "package.json"


def _summary_body(result: ScanResult, inline_count: int, overflow: list[Finding]) -> str:
    if not result.findings and not result.license_issues:
        return (
            "## ✅ Oneport Depcheck\n\n"
            f"No known vulnerabilities in {result.packages_scanned} scanned package(s). "
            "Sources: OSV.dev."
        )

    lines = ["## Oneport Depcheck\n"]

    reach_counts = result.counts_by_reachability()
    sev_counts = result.counts_by_severity()
    lines.append("| Exploitability | Count | · | Severity | Count |")
    lines.append("|---|---|---|---|---|")
    reach_order = [r.value for r in (
        Reachability.REACHABLE, Reachability.UNTRIAGED,
        Reachability.LIKELY_UNREACHABLE, Reachability.DEV_ONLY,
    )]
    sev_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
    for i in range(max(len(reach_order), len(sev_order))):
        left = reach_order[i] if i < len(reach_order) else ""
        right = sev_order[i] if i < len(sev_order) else ""
        lines.append(
            f"| {left} | {reach_counts.get(left, 0) if left else ''} | · "
            f"| {right} | {sev_counts.get(right, 0) if right else ''} |"
        )

    top = [f for f in result.findings if f.reachability == Reachability.REACHABLE][:3]
    if top:
        lines.append("\n**Fix these first (reachable in your code):**")
        for f in top:
            fix = f" → upgrade to `{f.upgrade_target}`" if f.upgrade_target else ""
            lines.append(
                f"1. 🔴 **{f.cve}** `{f.spec}` "
                f"({f.manifest}:{f.manifest_line}){fix} — {f.reachability_reason}"
            )

    if inline_count:
        lines.append(f"\n{inline_count} finding(s) posted as inline comments on manifest lines.")

    if overflow:
        lines.append("\n<details><summary>All findings</summary>\n")
        lines.append("| # | Severity | Exploitability | Package | Advisory | Manifest | Fix |")
        lines.append("|---|---|---|---|---|---|---|")
        for i, f in enumerate(overflow, start=1):
            emoji = _REACH_EMOJI[f.reachability]
            fix = f"`{f.upgrade_target}`" if f.upgrade_target else "—"
            lines.append(
                f"| {i} | {f.severity.value} | {emoji} {f.reachability.value} "
                f"| `{f.spec}` | {f.cve} "
                f"| `{f.manifest}:{f.manifest_line}` | {fix} |"
            )
        lines.append("\n</details>")

    if result.license_issues:
        lines.append(f"\n**License issues ({len(result.license_issues)}):**")
        for li in result.license_issues:
            lines.append(
                f"- **{li.risk}** `{li.package}` — {li.license} "
                f"(`{li.manifest}:{li.manifest_line}`). {li.detail}"
            )

    if result.skipped_unpinned:
        lines.append(
            f"\n*{len(result.skipped_unpinned)} unpinned requirement(s) could not be "
            "checked (no exact version): " + ", ".join(result.skipped_unpinned) + "*"
        )

    triage_note = (
        f"triage: `{result.model}`" if result.triaged else "triage: skipped (--no-llm)"
    )
    lines.append(
        f"\n---\n*Detection: [OSV.dev](https://osv.dev) (deterministic) · "
        f"{triage_note} · [oneport-depcheck](https://oneport.dev)*"
    )
    return "\n".join(lines)

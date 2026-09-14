"""
GitHub PR review builder — the payload for `POST .../pulls/{n}/reviews`.

Findings whose (file, line) falls inside the diff become inline, per-line
comments. Findings outside the visible diff are never silently dropped —
they're listed in the top-level review body instead. The body carries the
verdict, the safe rewrite plan, and a hidden reviewed-SHA marker so a re-run
on the same head skips the duplicate post.
"""

from __future__ import annotations

from oneport_migrate.diff_utils import commentable_lines
from oneport_migrate.markers import REVIEWED_MARKER_TPL
from oneport_migrate.result import CheckResult, Finding, Severity

_SEVERITY_EMOJI = {
    Severity.CRITICAL: "🔴",
    Severity.ERROR:    "🟠",
    Severity.WARNING:  "🟡",
    Severity.INFO:     "ℹ️",
}


def build_pr_review(result: CheckResult, diff_text: str) -> dict:
    """
    Build the payload for GitHubIntegration.post_review() from a CheckResult.

    Returns:
        {"body": str, "comments": [{"path", "line", "body"}, ...], "event": str}
    """
    diff_lines = commentable_lines(diff_text)

    inline: list[Finding] = []
    overflow: list[Finding] = []
    for finding in result.findings:
        if finding.line in diff_lines.get(finding.file, set()):
            inline.append(finding)
        else:
            overflow.append(finding)

    comments = [
        {"path": f.file, "line": f.line, "body": _comment_body(f)}
        for f in inline
    ]

    body = _review_body(result, inline_count=len(inline), overflow=overflow)

    head_sha = (result.pr_ref or {}).get("head_sha", "")
    if head_sha:
        body += "\n\n" + REVIEWED_MARKER_TPL.format(sha=head_sha)

    return {"body": body, "comments": comments, "event": "COMMENT"}


def _comment_body(f: Finding) -> str:
    emoji = _SEVERITY_EMOJI[f.severity]
    parts = [f"{emoji} **`{f.rule_id}`** — {f.message}", "", f.suggestion]
    if f.blast_radius:
        parts += ["", f"**Blast radius:** {f.blast_radius}"]
    if f.snippet:
        parts += ["", f"```sql\n{f.snippet.strip()}\n```"]
    return "\n".join(parts)


def _review_body(result: CheckResult, inline_count: int, overflow: list[Finding]) -> str:
    if not result.findings:
        body = "## ✅ Oneport Migrate\n\nNo migration-safety findings."
        if result.verdict:
            body += f"\n\n{result.verdict}"
        return body

    counts = {
        "Critical": len(result.critical),
        "Error":    len(result.errors),
        "Warning":  len(result.warnings),
        "Info":     len([f for f in result.findings if f.severity == Severity.INFO]),
    }
    rows = [f"| {sev} | {count} |" for sev, count in counts.items()]
    table = "| Severity | Count |\n|----------|-------|\n" + "\n".join(rows)

    lines = ["## Oneport Migrate — migration safety review\n", table]

    if result.verdict:
        lines.append(f"\n**Verdict:** {result.verdict}")

    if result.rewrite_plan:
        lines.append("\n**Safe rewrite plan:**")
        for i, step in enumerate(result.rewrite_plan, 1):
            lines.append(f"{i}. {step}")

    if inline_count:
        lines.append(f"\n{inline_count} finding(s) posted as inline comments below.")

    if overflow:
        lines.append(
            "\nThe following finding(s) are outside the visible diff and couldn't be "
            "anchored to a line — reported here instead:\n"
        )
        for f in overflow:
            emoji = _SEVERITY_EMOJI[f.severity]
            location = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"- {emoji} `{f.rule_id}` **{location}** — {f.message}")

    if result.llm_note:
        lines.append(f"\n*{result.llm_note}*")

    lines.append(
        f"\n---\n*Checked by [Oneport Migrate](https://oneport.dev) · db: `{result.db}`"
        + (f" · model: `{result.model}`" if result.model else "")
        + "*"
    )
    return "\n".join(lines)

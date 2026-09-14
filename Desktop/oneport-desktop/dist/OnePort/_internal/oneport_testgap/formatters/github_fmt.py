"""
GitHub formatter — builds the payload for posting the gap report as an inline
PR review (same machinery as oneport-review: per-gap inline comments anchored
to diff lines, everything else in the review body, hidden marker for state).
"""

from __future__ import annotations

from oneport_testgap.diff_utils import commentable_lines
from oneport_testgap.gaps import Gap, GapReport, Risk
from oneport_testgap.markers import TESTGAP_MARKER_TPL

_RISK_EMOJI = {
    Risk.CRITICAL: "🔴",
    Risk.HIGH:     "🟠",
    Risk.MEDIUM:   "🟡",
    Risk.LOW:      "ℹ️",
    Risk.UNRANKED: "⚪",
}


def build_pr_review(report: GapReport, diff_text: str, min_risk: str = "low") -> dict:
    """
    Build the payload for GitHubIntegration.post_review() from a GapReport.

    Gaps whose first line falls inside the diff are posted as inline, per-line
    comments. Gaps that fall outside the visible diff are never silently
    dropped — they're listed in the top-level review body instead.

    Returns:
        {"body": str, "comments": [{"path", "line", "body"}, ...], "event": str}
    """
    diff_lines = commentable_lines(diff_text)
    shown = report.gaps_at_least(min_risk)

    inline_gaps: list[Gap] = []
    overflow_gaps: list[Gap] = []
    for gap in shown:
        anchor = _anchor_line(gap, diff_lines.get(gap.file, set()))
        if anchor is not None:
            inline_gaps.append(gap)
        else:
            overflow_gaps.append(gap)

    comments = [
        {
            "path": gap.file,
            "line": _anchor_line(gap, diff_lines.get(gap.file, set())),
            "body": _inline_comment_body(gap),
        }
        for gap in inline_gaps
    ]

    body = _review_summary(report, inline_count=len(inline_gaps), overflow=overflow_gaps)

    # Stamp the analyzed head SHA (hidden) — the PR itself is the state store.
    head_sha = (report.pr_ref or {}).get("head_sha", "")
    if head_sha:
        body += "\n\n" + TESTGAP_MARKER_TPL.format(sha=head_sha)

    return {"body": body, "comments": comments, "event": "COMMENT"}


def _anchor_line(gap: Gap, file_commentable: set[int]) -> int | None:
    """First gap line GitHub will accept an inline comment on, or None."""
    for line in gap.lines:
        if line in file_commentable:
            return line
    return None


def _inline_comment_body(gap: Gap) -> str:
    emoji = _RISK_EMOJI[gap.risk]
    parts = [
        f"{emoji} **Test gap ({gap.risk.value})** — `{gap.function}` "
        f"lines {gap.line_ranges} were changed but never executed by the test suite."
    ]
    if gap.why:
        parts += ["", gap.why]
    if not gap.measured:
        parts += ["", "*This file wasn't measured by the coverage run at all.*"]
    return "\n".join(parts)


def _review_summary(report: GapReport, inline_count: int, overflow: list[Gap]) -> str:
    if not report.gaps:
        return (
            "## ✅ Oneport Testgap\n\n"
            "Every changed line is covered by the test suite."
        )

    counts = {
        "Critical": sum(1 for g in report.gaps if g.risk == Risk.CRITICAL),
        "High":     sum(1 for g in report.gaps if g.risk == Risk.HIGH),
        "Medium":   sum(1 for g in report.gaps if g.risk == Risk.MEDIUM),
        "Low":      sum(1 for g in report.gaps if g.risk == Risk.LOW),
    }
    rows = [f"| {risk} | {count} |" for risk, count in counts.items()]
    table = "| Risk | Untested gaps |\n|------|---------------|\n" + "\n".join(rows)

    lines = [
        "## Oneport Testgap\n",
        f"{report.gap_line_count} of {report.changed_line_count} changed line(s) "
        f"have **zero test coverage** across {report.files_analyzed} file(s).\n",
        table,
    ]
    if inline_count:
        lines.append(f"\n{inline_count} gap(s) annotated inline below.")

    if overflow:
        lines.append(
            "\nThe following gap(s) are outside the visible diff and couldn't be "
            "anchored to a line — reported here instead:\n"
        )
        for gap in overflow:
            emoji = _RISK_EMOJI[gap.risk]
            lines.append(
                f"- {emoji} **{gap.file}:{gap.line_ranges}** (`{gap.function}`) — "
                f"{gap.why or 'changed but never executed by tests'}"
            )

    verified = [t for t in report.generated if t.verified]
    if verified:
        lines.append("\n### Verified generated tests\n")
        lines.append(
            "These tests were **executed with pytest and confirmed to cover the gap "
            "lines** before being shown (failing generations are discarded, not posted):\n"
        )
        for t in verified:
            lines.append(
                f"- `{t.path}` — covers {len(t.newly_covered)}/{len(t.gap.lines)} "
                f"gap line(s) of `{t.gap.file}::{t.gap.function}`"
            )

    lines.append(
        f"\n---\n*Analyzed by [Oneport Testgap](https://oneport.dev) · "
        f"model: `{report.model}` (risk ranking only — coverage facts come from pytest-cov)*"
    )
    return "\n".join(lines)

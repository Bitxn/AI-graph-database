"""
GitHub formatter — produces markdown suitable for posting as a PR review comment
or GitHub Checks annotation via the REST API.
"""

from __future__ import annotations

import json

from oneport.diff_utils import commentable_lines
from oneport.formatters.base import Formatter
from oneport.result import ReviewResult, Issue, Severity

_SEVERITY_TO_ANNOTATION = {
    Severity.CRITICAL: "failure",
    Severity.ERROR:    "failure",
    Severity.WARNING:  "warning",
    Severity.INFO:     "notice",
}

_SEVERITY_EMOJI = {
    Severity.CRITICAL: "🔴",
    Severity.ERROR:    "🟠",
    Severity.WARNING:  "🟡",
    Severity.INFO:     "ℹ️",
}


class GitHubFormatter(Formatter):
    """
    Produces two artefacts bundled in a JSON envelope:
      - "comment_body": Markdown string for a PR comment
      - "annotations":  List of GitHub Checks annotation objects
    """

    def format(self, result: ReviewResult) -> str:
        return json.dumps({
            "comment_body": self._comment_body(result),
            "annotations":  self._annotations(result),
        }, indent=2)

    # ── Comment body ──────────────────────────────────────────────────────────

    def _comment_body(self, result: ReviewResult) -> str:
        if not result.issues:
            return "## ✅ Oneport Review\n\nNo issues found."

        lines = ["## Oneport Review\n"]
        lines.append(self._summary_table(result))

        for issue in result.issues:
            emoji = _SEVERITY_EMOJI[issue.severity]
            lines.append(
                f"\n### {emoji} `{issue.rule_id}` — {issue.message}\n"
                f"**File:** `{issue.file}` · **Line:** {issue.line}  \n"
                f"**Severity:** {issue.severity.value}  \n"
                f"\n{issue.suggestion}\n"
            )
            if issue.snippet:
                lines.append(f"```\n{issue.snippet.strip()}\n```\n")

        lines.append(
            f"\n---\n*Reviewed by [Oneport](https://oneport.dev) · "
            f"model: `{result.model}`*"
        )
        return "\n".join(lines)

    def _summary_table(self, result: ReviewResult) -> str:
        counts = {
            "Critical": len(result.critical),
            "Error":    len(result.errors),
            "Warning":  len(result.warnings),
            "Info":     len([i for i in result.issues if i.severity == Severity.INFO]),
        }
        rows = [f"| {sev} | {count} |" for sev, count in counts.items()]
        return "| Severity | Count |\n|----------|-------|\n" + "\n".join(rows)

    # ── Annotations ───────────────────────────────────────────────────────────

    def _annotations(self, result: ReviewResult) -> list[dict]:
        return [
            {
                "path":             issue.file,
                "start_line":       issue.line,
                "end_line":         issue.location.end_line or issue.line,
                "annotation_level": _SEVERITY_TO_ANNOTATION[issue.severity],
                "title":            f"{issue.rule_id}: {issue.message[:100]}",
                "message":          issue.suggestion,
            }
            for issue in result.issues
        ]


# ── Inline PR review (Pull Request Reviews API) ────────────────────────────────
#
# Separate from GitHubFormatter.format() above: that method targets the Checks
# API annotation shape, whereas this targets `POST .../pulls/{n}/reviews`, which
# needs the diff text (not just the ReviewResult) to know which lines GitHub
# will actually accept an inline comment on.

def build_pr_review(result: ReviewResult, diff_text: str) -> dict:
    """
    Build the payload for GitHubIntegration.post_review() from a ReviewResult.

    Issues whose (file, line) falls inside the diff are posted as inline,
    per-line comments — what shows up as red squiggles in "Files changed".
    Issues that fall outside the diff view (e.g. the model reported a line
    number GitHub won't accept, or a full-file review with no diff hunks) are
    never silently dropped — they're listed in the top-level review body instead.

    Returns:
        {"body": str, "comments": [{"path", "line", "body"}, ...], "event": str}
    """
    diff_lines = commentable_lines(diff_text)

    inline_issues: list[Issue] = []
    overflow_issues: list[Issue] = []
    for issue in result.issues:
        if issue.line in diff_lines.get(issue.file, set()):
            inline_issues.append(issue)
        else:
            overflow_issues.append(issue)

    comments = [
        _inline_comment(issue, diff_lines.get(issue.file, set()))
        for issue in inline_issues
    ]

    body = _review_summary(result, inline_count=len(inline_issues), overflow=overflow_issues)

    # Stamp the reviewed head SHA (hidden) so the next run can review only the
    # commits pushed since — the PR itself is the state store.
    head_sha = (result.pr_ref or {}).get("head_sha", "")
    if head_sha:
        from oneport.markers import REVIEWED_MARKER_TPL
        body += "\n\n" + REVIEWED_MARKER_TPL.format(sha=head_sha)

    return {
        "body": body,
        "comments": comments,
        "event": "COMMENT",
    }


def _inline_comment(issue: Issue, file_commentable: set[int]) -> dict:
    """
    Build one review-comment payload, attaching a committable ```suggestion
    block when the fix can be applied safely.

    A GitHub suggestion replaces EXACTLY the line range the comment is anchored
    to, so the anchor must match the range the fix targets:
      - fix targets one line          → single-line comment on that line
      - fix targets line..end_line    → multi-line comment (start_line + line),
                                        valid only if every line in the range is
                                        part of the diff
      - range not fully in the diff   → plain comment, fix shown as a normal
                                        code block (still copy-pasteable, just
                                        not one-click committable)
    """
    start = issue.line
    end = issue.location.end_line or issue.line

    range_commentable = all(n in file_commentable for n in range(start, end + 1))
    # A fix containing a triple-backtick fence can't be embedded in a
    # ```suggestion block without breaking GitHub's markdown parsing.
    can_suggest = bool(issue.fix) and range_commentable and "```" not in issue.fix

    comment: dict = {"path": issue.file, "line": end if can_suggest else issue.line}
    if can_suggest and end > start:
        comment["start_line"] = start

    comment["body"] = _inline_comment_body(issue, include_suggestion=can_suggest)
    return comment


def _inline_comment_body(issue: Issue, include_suggestion: bool = False) -> str:
    emoji = _SEVERITY_EMOJI[issue.severity]
    parts = [f"{emoji} **`{issue.rule_id}`** — {issue.message}", "", issue.suggestion]
    if include_suggestion:
        parts += ["", f"```suggestion\n{issue.fix.rstrip()}\n```"]
    else:
        if issue.snippet:
            parts += ["", f"```\n{issue.snippet.strip()}\n```"]
        if issue.fix and "```" not in issue.fix:
            parts += ["", "Proposed fix:", f"```\n{issue.fix.rstrip()}\n```"]
    return "\n".join(parts)


def _review_summary(result: ReviewResult, inline_count: int, overflow: list[Issue]) -> str:
    scope_note = ""
    if result.incremental_from:
        scope_note = (
            f"\n\n*Incremental review — only the commits since "
            f"`{result.incremental_from[:7]}` (previously reviewed) were re-examined.*"
        )

    if not result.issues:
        return "## ✅ Oneport Review\n\nNo issues found." + scope_note

    counts = {
        "Critical": len(result.critical),
        "Error":    len(result.errors),
        "Warning":  len(result.warnings),
        "Info":     len([i for i in result.issues if i.severity == Severity.INFO]),
    }
    rows = [f"| {sev} | {count} |" for sev, count in counts.items()]
    table = "| Severity | Count |\n|----------|-------|\n" + "\n".join(rows)

    lines = ["## Oneport Review" + scope_note + "\n", table]
    if inline_count:
        lines.append(f"\n{inline_count} issue(s) posted as inline comments below.")

    if overflow:
        lines.append(
            "\nThe following issue(s) are outside the visible diff and couldn't be "
            "anchored to a line — reported here instead:\n"
        )
        for issue in overflow:
            emoji = _SEVERITY_EMOJI[issue.severity]
            lines.append(
                f"- {emoji} `{issue.rule_id}` **{issue.file}:{issue.line}** — {issue.message}"
            )

    lines.append(f"\n---\n*Reviewed by [Oneport](https://oneport.dev) · model: `{result.model}`*")
    return "\n".join(lines)

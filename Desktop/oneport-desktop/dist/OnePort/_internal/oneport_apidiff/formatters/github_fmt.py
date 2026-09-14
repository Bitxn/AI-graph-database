"""
GitHub formatter — the --post payloads.

Two artefacts, mirroring oneport-review's github_fmt.py:
  - a sticky verdict-table comment (upserted in place via STICKY_MARKER, with
    a CHECKED_MARKER carrying the head SHA so re-runs update, not duplicate)
  - an inline PR review anchoring each change to its changed signature line
    in "Files changed"; changes whose line isn't visible in the diff are never
    silently dropped — they're listed in the review body instead.
"""

from __future__ import annotations

from oneport_apidiff.diff_utils import commentable_lines
from oneport_apidiff.markers import CHECKED_MARKER_TPL, STICKY_MARKER
from oneport_apidiff.result import ApiDiffResult, Change, Verdict

_EMOJI = {
    Verdict.BREAKING: "🔴",
    Verdict.RISKY: "🟡",
    Verdict.COMPATIBLE: "🟢",
}


# ── Sticky verdict-table comment ───────────────────────────────────────────────


def build_sticky_comment(result: ApiDiffResult) -> str:
    head_sha = (result.pr_ref or {}).get("head_sha", "")
    overall = _EMOJI[Verdict(result.verdict)] + " **" + result.verdict + "**"

    lines = ["## Oneport ApiDiff — breaking-change gate", ""]
    if not result.changes:
        lines.append("🟢 **COMPATIBLE** — no public API surface changes detected.")
    else:
        lines.append(f"Overall verdict: {overall}")
        lines.append("")
        lines.append("| | Symbol | Change | Consumer impact | Location |")
        lines.append("|---|--------|--------|-----------------|----------|")
        ordered = result.breaking + result.risky + result.compatible
        for c in ordered:
            impact = c.impact or "—"
            lines.append(
                f"| {_EMOJI[c.verdict]} {c.verdict.value} | `{c.symbol}` | "
                f"{_escape_cell(c.detail)} | {_escape_cell(impact)} | "
                f"`{c.file}:{c.line}` |"
            )

        broken_files = sorted(
            {ref.file for c in result.breaking for ref in c.callers}
        )
        if broken_files:
            lines.append("")
            lines.append(
                "**Internal callers affected:** " + ", ".join(f"`{f}`" for f in broken_files)
            )

        migrations = [c for c in result.breaking + result.risky if c.migration]
        if migrations:
            lines.append("")
            lines.append("<details><summary>Migration notes</summary>")
            lines.append("")
            for c in migrations:
                lines.append(f"- `{c.symbol}` — {c.migration}")
            lines.append("")
            lines.append("</details>")

    lines.append("")
    lines.append(
        "---\n*Checked by [Oneport ApiDiff](https://oneport.dev) · "
        + (f"model: `{result.model}`*" if result.llm_used else "deterministic verdicts*")
    )
    lines.append("")
    lines.append(STICKY_MARKER)
    if head_sha:
        lines.append(CHECKED_MARKER_TPL.format(sha=head_sha))
    return "\n".join(lines)


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


# ── Inline PR review ───────────────────────────────────────────────────────────


def build_pr_review(result: ApiDiffResult, diff_text: str) -> dict:
    """
    Build the payload for GitHubIntegration.post_review().

    Changes whose head line is visible in the diff become inline comments on
    the exact changed signature; the rest (e.g. removed symbols — their line
    no longer exists in the new file) are summarised in the review body.

    Returns {"body": str, "comments": [...], "event": "COMMENT"}.
    """
    diff_lines = commentable_lines(diff_text)

    inline: list[Change] = []
    overflow: list[Change] = []
    for change in result.changes:
        if change.verdict == Verdict.COMPATIBLE:
            continue  # only breaking/risky earn an inline comment — keep PRs quiet
        if change.line in diff_lines.get(change.file, set()):
            inline.append(change)
        else:
            overflow.append(change)

    comments = [
        {
            "path": c.file,
            "line": c.line,
            "body": _inline_comment_body(c),
        }
        for c in inline
    ]

    return {
        "body": _review_body(result, inline_count=len(inline), overflow=overflow),
        "comments": comments,
        "event": "COMMENT",
    }


def _inline_comment_body(change: Change) -> str:
    parts = [f"{_EMOJI[change.verdict]} **{change.verdict.value}** — {change.detail}"]
    if change.old_signature and change.new_signature:
        parts += [
            "",
            f"```diff\n- {change.old_signature}\n+ {change.new_signature}\n```",
        ]
    if change.impact:
        parts += ["", change.impact]
    if change.callers:
        refs = ", ".join(f"`{r.file}:{r.line}`" for r in change.callers[:5])
        more = f" (+{len(change.callers) - 5} more)" if len(change.callers) > 5 else ""
        parts += ["", f"**Internal callers:** {refs}{more}"]
    if change.migration:
        parts += ["", f"**Migration:** {change.migration}"]
    return "\n".join(parts)


def _review_body(result: ApiDiffResult, inline_count: int, overflow: list[Change]) -> str:
    if not result.changes:
        return "## 🟢 Oneport ApiDiff\n\nNo public API surface changes detected."

    lines = [f"## {_EMOJI[Verdict(result.verdict)]} Oneport ApiDiff — {result.verdict}", ""]
    lines.append(
        f"{len(result.breaking)} breaking · {len(result.risky)} risky · "
        f"{len(result.compatible)} compatible"
    )
    if inline_count:
        lines.append(f"\n{inline_count} change(s) annotated inline on the exact signatures.")

    if overflow:
        lines.append(
            "\nThe following change(s) have no anchorable line in the diff "
            "(e.g. the symbol was removed) — reported here instead:\n"
        )
        for c in overflow:
            entry = f"- {_EMOJI[c.verdict]} **{c.verdict.value}** `{c.symbol}` — {c.detail}"
            if c.impact:
                entry += f" — {c.impact}"
            if c.callers:
                refs = ", ".join(f"`{r.file}:{r.line}`" for r in c.callers[:5])
                entry += f"\n  - internal callers: {refs}"
            lines.append(entry)

    lines.append(
        "\n---\n*Checked by [Oneport ApiDiff](https://oneport.dev) · "
        + (f"model: `{result.model}`*" if result.llm_used else "deterministic verdicts*")
    )
    return "\n".join(lines)

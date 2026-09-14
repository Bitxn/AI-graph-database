"""
Inline (terminal) formatter — plain text, grouped by verdict, worst first.
"""

from __future__ import annotations

from oneport_apidiff.result import ApiDiffResult, Change, Verdict

_BADGE = {
    Verdict.BREAKING: "[BREAKING]  ",
    Verdict.RISKY: "[RISKY]     ",
    Verdict.COMPATIBLE: "[COMPATIBLE]",
}


def format_inline(result: ApiDiffResult) -> str:
    lines: list[str] = []
    header = f"oneport-apidiff — {result.target}"
    lines.append(header)
    lines.append("=" * len(header))

    if not result.changes:
        lines.append("")
        lines.append("No public API surface changes detected. Ship it.")
        return "\n".join(lines)

    waived_note = f", {len(result.waived)} waived" if result.waived else ""
    lines.append(
        f"Verdict: {result.verdict}   "
        f"({len(result.breaking)} breaking, {len(result.risky)} risky, "
        f"{len(result.compatible)} compatible{waived_note}; "
        f"{result.files_checked} file(s) checked)"
    )

    for group in (result.breaking, result.risky, result.compatible):
        for change in group:
            lines.append("")
            waived_tag = "  [WAIVED]" if change.waived else ""
            lines.append(
                f"{_BADGE[change.verdict]} {change.symbol}  "
                f"({change.file}:{change.line}){waived_tag}"
            )
            lines.append(f"    {change.detail}")
            if change.waived:
                reason = f" — {change.waiver_reason}" if change.waiver_reason else ""
                lines.append(f"    waived: does not fail the gate{reason}")
            if change.old_signature and change.new_signature:
                lines.append(f"    - {change.old_signature}")
                lines.append(f"    + {change.new_signature}")
            elif change.old_signature:
                lines.append(f"    - {change.old_signature}")
            elif change.new_signature:
                lines.append(f"    + {change.new_signature}")
            if change.impact:
                lines.append(f"    impact:    {change.impact}")
            if change.migration:
                lines.append(f"    migration: {change.migration}")
            if change.callers:
                lines.append(f"    internal callers ({len(change.callers)}):")
                for ref in change.callers[:5]:
                    lines.append(f"      {ref.file}:{ref.line}  {ref.snippet[:80]}")
                if len(change.callers) > 5:
                    lines.append(f"      ... and {len(change.callers) - 5} more")

    lines.append("")
    footer = _footer(result)
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def _footer(result: ApiDiffResult) -> str:
    if result.llm_used:
        return (
            f"classified by {result.model} · {result.total_tokens} tokens · "
            f"{result.elapsed_ms} ms"
        )
    return "deterministic verdicts only (no model used) · " f"{result.elapsed_ms} ms"


def summary_line(change: Change) -> str:
    """One-line rendering used by tests and quick logs."""
    return f"{change.verdict.value}: {change.symbol} — {change.detail}"

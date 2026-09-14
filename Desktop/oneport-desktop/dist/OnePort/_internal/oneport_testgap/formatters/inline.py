"""
Inline (terminal) formatter — the default `analyze` output.

Plain text with severity tags, readable both in a terminal and in raw CI logs.
"""

from __future__ import annotations

from oneport_testgap.gaps import GapReport, Risk

_RISK_TAG = {
    Risk.CRITICAL: "[CRITICAL]",
    Risk.HIGH:     "[HIGH]    ",
    Risk.MEDIUM:   "[MEDIUM]  ",
    Risk.LOW:      "[LOW]     ",
    Risk.UNRANKED: "[UNRANKED]",
}


def format_inline(report: GapReport, min_risk: str = "low") -> str:
    lines: list[str] = []
    lines.append(f"Oneport Testgap - {report.target}")
    lines.append(
        f"{report.files_analyzed} file(s) analyzed | "
        f"{report.changed_line_count} changed line(s) | "
        f"{report.gap_line_count} with zero coverage"
    )
    lines.append("")

    shown = report.gaps_at_least(min_risk)
    if not report.gaps:
        lines.append("No test gaps: every changed line is covered by the test suite. [OK]")
        return "\n".join(lines)
    if not shown:
        lines.append(
            f"No gaps at or above min-risk={min_risk} "
            f"({len(report.gaps)} lower-risk gap(s) hidden)."
        )
        return "\n".join(lines)

    for gap in shown:
        tag = _RISK_TAG[gap.risk]
        waived = "  [WAIVED]" if gap.waived else ""
        lines.append(f"{tag} {gap.file}:{gap.line_ranges}  ({gap.function}){waived}")
        if gap.why:
            lines.append(f"           {gap.why}")
        if gap.waived:
            reason = f" — {gap.waiver_reason}" if gap.waiver_reason else ""
            lines.append(f"           waived: does not block CI{reason}")
        if not gap.measured:
            lines.append("           (file was not measured by the coverage run at all)")
        lines.append("")

    hidden = len(report.gaps) - len(shown)
    if hidden:
        lines.append(f"({hidden} lower-risk gap(s) hidden — rerun with --min-risk low)")
        lines.append("")

    if report.generated:
        lines.append("Generated tests (only VERIFIED tests are written to disk):")
        for t in report.generated:
            if t.verified:
                lines.append(
                    f"  VERIFIED  {t.path} — passed, covers "
                    f"{len(t.newly_covered)}/{len(t.gap.lines)} gap line(s) "
                    f"of {t.gap.file} [{t.attempts} attempt(s)]"
                )
            else:
                lines.append(
                    f"  DISCARDED {t.gap.file}::{t.gap.function} — "
                    f"failed execution/coverage verification after {t.attempts} attempt(s)"
                )
        lines.append("")

    if report.has_critical_gaps:
        lines.append("RESULT: critical-risk changed lines have zero coverage — exit 1 (CI gate).")
    else:
        lines.append("RESULT: no critical-risk gaps.")
    return "\n".join(lines)

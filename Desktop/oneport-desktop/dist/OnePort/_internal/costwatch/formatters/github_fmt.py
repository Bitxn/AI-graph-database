"""
GitHub formatter — markdown for a PR comment.

`build_pr_comment` produces the sticky "cost diff" comment that `analyze --post`
upserts on a PR: it leads with the provisioned-cost delta this PR introduces,
then lists each waste finding with its saving and the cheaper config. The
hidden marker keeps it to one always-current comment instead of one per push.
"""

from __future__ import annotations

import json

from costwatch.formatters.base import Formatter
from costwatch.markers import COST_COMMENT_MARKER
from costwatch.result import CostReport, Finding, Severity

_SEVERITY_EMOJI = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH:     "🟠",
    Severity.WARNING:  "🟡",
    Severity.INFO:     "🔵",
}


class GitHubFormatter(Formatter):
    """Standalone markdown body (no cost-diff header) for a plain `--format github` run."""

    def format(self, report: CostReport) -> str:
        return json.dumps({"comment_body": _body(report, header=_plain_header(report))}, indent=2)


def build_pr_comment(report: CostReport) -> str:
    """Build the sticky PR comment body for a cost-diff (--post) run."""
    return _body(report, header=_diff_header(report)) + "\n\n" + COST_COMMENT_MARKER


def _diff_header(report: CostReport) -> str:
    delta = report.cost_delta or 0.0
    if delta > 0:
        return (
            f"## 💸 Oneport Costwatch — this PR adds **~${delta:,.0f}/mo**\n\n"
            f"This change increases provisioned cost. See the cheaper configs below."
        )
    if delta < 0:
        return f"## ✅ Oneport Costwatch — this PR saves **~${abs(delta):,.0f}/mo**\n"
    return "## Oneport Costwatch — no net provisioned-cost change\n"


def _plain_header(report: CostReport) -> str:
    return (
        f"## Oneport Costwatch\n\n"
        f"Approximate provisioned cost: **~${report.total_monthly_cost:,.0f}/mo** "
        f"across {len(report.resources)} resource(s)."
    )


def _body(report: CostReport, header: str) -> str:
    lines = [header, ""]

    if not report.findings:
        lines.append("No waste detected — this config looks lean. ✅")
        lines.append(_footer(report))
        return "\n".join(lines)

    lines.append(
        f"**Potential savings: ~${report.estimated_monthly_savings:,.0f}/mo** "
        f"across {len(report.findings)} finding(s).\n"
    )
    lines.append("| Severity | Resource | Save/mo | Fix |")
    lines.append("|----------|----------|--------:|-----|")
    for f in report.findings:
        lines.append(_row(f))

    lines.append("")
    for f in report.findings:
        lines += _detail(f)

    lines.append(_footer(report))
    return "\n".join(lines)


def _row(f: Finding) -> str:
    emoji = _SEVERITY_EMOJI[f.severity]
    fix = f"`{f.suggested_config}`" if f.suggested_config else "—"
    return (
        f"| {emoji} {f.severity.value} | `{f.resource}` | "
        f"${f.estimated_monthly_saving:,.0f} | {fix} |"
    )


def _detail(f: Finding) -> list[str]:
    emoji = _SEVERITY_EMOJI[f.severity]
    loc = f"`{f.file}`:{f.line}" if f.line else (f"`{f.file}`" if f.file else "")
    out = [
        f"### {emoji} `{f.resource}` — save ~${f.estimated_monthly_saving:,.0f}/mo "
        f"_({f.category})_",
        "",
        f"{f.message}",
    ]
    if f.current_config or f.suggested_config:
        out += [
            "",
            "```diff",
            f"- {f.current_config}",
            f"+ {f.suggested_config}",
            "```",
        ]
    if loc:
        out.append(f"\n{loc}")
    out.append("")
    return out


def _footer(report: CostReport) -> str:
    return (
        f"\n---\n*Estimates are approximate (bundled price table, no cloud "
        f"credentials used) · model: `{report.model}`*"
    )

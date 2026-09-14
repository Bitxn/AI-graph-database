"""
Inline (terminal) formatter — the default for `oneport-costwatch analyze`.

Renders a compact cost summary and each waste finding with its estimated
monthly saving and the concrete config change, using rich markup.
"""

from __future__ import annotations

from costwatch.formatters.base import Formatter
from costwatch.result import CostReport, Finding, Severity

_SEVERITY_STYLE = {
    Severity.CRITICAL: ("🔴", "bold red"),
    Severity.HIGH:     ("🟠", "bold yellow"),
    Severity.WARNING:  ("🟡", "yellow"),
    Severity.INFO:     ("🔵", "cyan"),
}


class InlineFormatter(Formatter):
    def format(self, report: CostReport) -> str:
        lines: list[str] = []

        total = report.total_monthly_cost
        priced = [r for r in report.resources if r.monthly_cost is not None]
        lines.append("[bold]Oneport Costwatch[/bold]")
        lines.append(
            f"  Parsed [bold]{len(report.resources)}[/bold] resource(s); "
            f"[bold]{len(priced)}[/bold] priced. "
            f"Approx provisioned cost: [bold]~${total:,.0f}/mo[/bold]"
        )
        if report.cost_delta is not None:
            sign = "+" if report.cost_delta >= 0 else ""
            style = "red" if report.cost_delta > 0 else "green"
            lines.append(
                f"  Cost change vs base: [{style}]{sign}${report.cost_delta:,.0f}/mo[/{style}]"
            )

        if not report.findings:
            # Only claim "no waste" when analysis actually ran (analyze), not for a
            # deterministic price-only report, which never looked for waste.
            if report.priced:
                lines.append("")
                lines.append("[green]✓ No waste detected — this config looks lean.[/green]")
            lines += self._notes(report)
            return "\n".join(lines)

        savings = report.estimated_monthly_savings
        lines.append(
            f"  Potential savings: [bold green]~${savings:,.0f}/mo[/bold green] "
            f"across [bold]{len(report.findings)}[/bold] finding(s)"
        )
        lines.append("")

        for f in report.findings:
            lines += self._finding(f)

        lines += self._notes(report)
        return "\n".join(lines)

    def _finding(self, f: Finding) -> list[str]:
        emoji, style = _SEVERITY_STYLE[f.severity]
        loc = f"{f.file}:{f.line}" if f.line else f.file
        waived_tag = " [dim][WAIVED][/dim]" if f.waived else ""
        head = (
            f"{emoji} [{style}]{f.severity.value.upper()}[/{style}] "
            f"[bold]{f.resource}[/bold]  "
            f"[green]save ~${f.estimated_monthly_saving:,.0f}/mo[/green]  "
            f"[dim]({f.category})[/dim]{waived_tag}"
        )
        out = [head, f"   {f.message}"]
        if f.waived:
            reason = f" — {f.waiver_reason}" if f.waiver_reason else ""
            out.append(f"   [dim]waived: does not block CI{reason}[/dim]")
        if f.current_config or f.suggested_config:
            out.append(
                f"   [red]- {f.current_config}[/red]  →  "
                f"[green]+ {f.suggested_config}[/green]"
            )
        if loc:
            out.append(f"   [dim]{loc}[/dim]")
        out.append("")
        return out

    def _notes(self, report: CostReport) -> list[str]:
        if not report.notes:
            return []
        out = ["", "[dim]Notes:[/dim]"]
        out += [f"[dim]  · {n}[/dim]" for n in report.notes]
        return out

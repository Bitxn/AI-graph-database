"""
Rich terminal formatter — coloured, human-readable inline output.
"""

from __future__ import annotations

import io

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from oneport_migrate.result import CheckResult, Severity

_SEVERITY_COLORS = {
    Severity.CRITICAL: "bold red",
    Severity.ERROR:    "red",
    Severity.WARNING:  "yellow",
    Severity.INFO:     "dim",
}

_SEVERITY_ICONS = {
    Severity.CRITICAL: "🔴",
    Severity.ERROR:    "🟠",
    Severity.WARNING:  "🟡",
    Severity.INFO:     "ℹ️ ",
}


class InlineFormatter:
    """Pretty terminal output using Rich."""

    def format(self, result: CheckResult) -> str:
        # Render into a buffer — the CLI prints the returned string exactly
        # once; force_terminal keeps ANSI colours in the export.
        console = Console(
            record=True, highlight=False, file=io.StringIO(),
            force_terminal=True, width=120,
        )

        if not result.findings:
            console.print("[bold green]✅ Migration check passed — no findings.[/bold green]")
        else:
            by_file: dict[str, list] = {}
            for finding in result.findings:
                by_file.setdefault(finding.file, []).append(finding)

            for file_path, findings in by_file.items():
                console.print(f"\n[bold underline]{file_path}[/bold underline]")
                for f in findings:
                    color = _SEVERITY_COLORS[f.severity]
                    icon = _SEVERITY_ICONS[f.severity]

                    header = Text()
                    header.append(f"{icon} ")
                    header.append(f"[{f.severity.value.upper()}]", style=color)
                    if f.line:
                        header.append(f"  Line {f.line}", style="dim")
                    header.append(f"  {f.rule_id}", style="dim")
                    if f.framework:
                        header.append(f"  ({f.framework})", style="dim")
                    if f.waived:
                        header.append("  [WAIVED]", style="dim")
                    console.print(header)

                    console.print(f"   [bold]{f.message}[/bold]")
                    if f.waived:
                        reason = f" — {f.waiver_reason}" if f.waiver_reason else ""
                        console.print(f"   [dim]waived: does not block CI{reason}[/dim]")
                    console.print(f"   [dim]Safe pattern:[/dim] {f.suggestion}")
                    if f.snippet:
                        console.print(f"   [dim]{f.snippet.strip()}[/dim]")
                    if f.blast_radius:
                        console.print(f"   [cyan]Blast radius:[/cyan] {f.blast_radius}")
                    console.print()

            self._print_summary(console, result)

        if result.verdict:
            console.print(Panel(result.verdict, title="Verdict", border_style="cyan"))

        if result.rewrite_plan:
            console.print("[bold]Safe rewrite plan:[/bold]")
            for i, step in enumerate(result.rewrite_plan, 1):
                console.print(f"  {i}. {step}")

        if result.llm_note:
            console.print(f"[yellow]{result.llm_note}[/yellow]")

        for note in result.notes:
            console.print(f"[dim]note: {note}[/dim]")

        self._print_footer(console, result)
        return console.export_text(styles=True)

    def _print_summary(self, console: Console, result: CheckResult) -> None:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("Severity")
        table.add_column("Count", justify="right")

        counts = {
            "critical": len(result.critical),
            "error":    len(result.errors),
            "warning":  len(result.warnings),
            "info":     len([f for f in result.findings if f.severity == Severity.INFO]),
        }
        colors = {"critical": "red", "error": "red", "warning": "yellow", "info": "dim"}
        for sev, count in counts.items():
            if count:
                table.add_row(Text(sev, style=colors[sev]), Text(str(count), style=colors[sev]))
        console.print(table)

    def _print_footer(self, console: Console, result: CheckResult) -> None:
        files = f"{len(result.files_checked)} migration file(s)"
        model = f" · model: {result.model}" if result.model else ""
        console.print(
            f"[dim]{files} · db: {result.db}{model} · {result.elapsed_ms}ms[/dim]"
        )

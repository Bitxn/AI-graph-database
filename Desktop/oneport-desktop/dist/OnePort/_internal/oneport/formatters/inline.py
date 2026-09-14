"""
Rich terminal formatter — coloured, human-readable inline output.
"""

from __future__ import annotations

import io

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from oneport.formatters.base import Formatter
from oneport.result import ReviewResult, Severity

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


class InlineFormatter(Formatter):
    """Pretty terminal output using Rich."""

    def format(self, result: ReviewResult) -> str:
        # Render into a buffer, not the real terminal — the CLI prints the
        # returned string exactly once. (Rendering straight to stdout here
        # caused every review to appear twice: once from this console, once
        # from the CLI printing the returned text.) styles=True keeps ANSI
        # colours in the export so the single print is still colourful.
        console = Console(
            record=True, highlight=False, file=io.StringIO(),
            force_terminal=True, width=120,
        )

        if not result.issues:
            # "Nothing was reviewed" must never render as "reviewed, clean" — a
            # commit touching only lockfiles/generated files hits this every time.
            if result.reviewed_nothing:
                shown = ", ".join(result.skipped_paths[:3])
                more = f" (+{len(result.skipped_paths) - 3} more)" if len(result.skipped_paths) > 3 else ""
                console.print("[bold yellow]⚠ Nothing to review[/bold yellow] — "
                              f"all {len(result.skipped_paths)} changed file(s) were skipped "
                              "as generated/vendored data.")
                console.print(f"   [dim]Skipped: {shown}{more}[/dim]")
            else:
                console.print("[bold green]✅ No issues found.[/bold green]")
                if result.skipped_paths:
                    console.print(f"   [dim]({len(result.skipped_paths)} generated/vendored "
                                  "file(s) skipped)[/dim]")
            self._print_footer(console, result)
            return console.export_text(styles=True)

        # Group issues by file
        by_file: dict[str, list] = {}
        for issue in result.issues:
            by_file.setdefault(issue.file, []).append(issue)

        for file_path, issues in by_file.items():
            console.print(f"\n[bold underline]{file_path}[/bold underline]")
            for issue in issues:
                color = _SEVERITY_COLORS[issue.severity]
                icon = _SEVERITY_ICONS[issue.severity]

                header = Text()
                header.append(f"{icon} ", style="")
                header.append(f"[{issue.severity.value.upper()}]", style=color)
                header.append(f"  Line {issue.line}", style="dim")
                header.append(f"  {issue.rule_id}", style="dim")
                if issue.waived:
                    header.append("  [WAIVED]", style="dim")
                console.print(header)

                console.print(f"   [bold]{issue.message}[/bold]")
                if issue.waived:
                    reason = f" — {issue.waiver_reason}" if issue.waiver_reason else ""
                    console.print(f"   [dim]waived: does not block CI{reason}[/dim]")
                if issue.suggestion.strip():
                    console.print(f"   [dim]Fix:[/dim] {issue.suggestion}")

                if issue.snippet:
                    console.print(f"   [dim]{issue.snippet.strip()}[/dim]")
                if issue.fix:
                    console.print("   [green]Suggested replacement:[/green]")
                    for fix_line in issue.fix.rstrip().splitlines():
                        console.print(f"   [green]+ {fix_line}[/green]")
                console.print()

        self._print_summary(console, result)
        self._print_footer(console, result)
        return console.export_text(styles=True)

    def _print_summary(self, console: Console, result: ReviewResult) -> None:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("Severity")
        table.add_column("Count", justify="right")

        counts = {
            "critical": len(result.critical),
            "error":    len(result.errors),
            "warning":  len(result.warnings),
            "info":     len([i for i in result.issues if i.severity == Severity.INFO]),
        }
        colors = {"critical": "red", "error": "red", "warning": "yellow", "info": "dim"}
        for sev, count in counts.items():
            if count:
                table.add_row(
                    Text(sev, style=colors[sev]),
                    Text(str(count), style=colors[sev]),
                )
        console.print(table)

    def _print_footer(self, console: Console, result: ReviewResult) -> None:
        cached_str = " [dim](cached)[/dim]" if result.cached else ""
        incremental_str = (
            f" · incremental since {result.incremental_from[:7]}"
            if result.incremental_from else ""
        )
        console.print(
            f"[dim]Model: {result.model} · {result.elapsed_ms}ms{incremental_str}{cached_str}[/dim]"
        )

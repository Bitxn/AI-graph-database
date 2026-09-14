"""Rich terminal rendering — the same content as markdown, styled for the console."""

from __future__ import annotations

from rich.console import Console

from oneport_standup.result import StandupReport

_TITLE = {"daily": "Standup", "weekly": "Weekly summary",
          "since": "Progress", "release": "Release notes"}


def render(report: StandupReport, console: Console | None = None) -> None:
    console = console or Console()
    console.print()
    title = _TITLE.get(report.mode, "Summary")
    console.print(f"[bold]{title}[/bold] [dim]— {report.range_label}[/dim]")
    if report.author_label != "everyone":
        console.print(f"[dim]{report.author_label}[/dim]")

    nar = report.narrative
    if not nar.is_empty:
        if nar.headline:
            console.print(f"\n[italic]{nar.headline}[/italic]")
        for g in nar.groups:
            if g.title:
                console.print(f"\n[bold]{g.title}[/bold]")
            for b in g.bullets:
                console.print(f"  • {b}")
    else:
        for day, commits in report.by_day().items():
            console.print(f"\n[bold]{day}[/bold]")
            for c in commits:
                refs = " ".join(c.tickets + c.prs)
                refs = f" [dim]({refs})[/dim]" if refs else ""
                console.print(f"  • {c.subject}{refs}")

    ins, dele = report.net_lines
    footer = (f"{len(report.commits)} commit(s) · {report.files_touched} file change(s) · "
              f"[green]+{ins}[/green]/[red]-{dele}[/red]")
    console.print(f"\n[dim]{footer}[/dim]")
    if report.tickets:
        console.print(f"[dim]refs: {', '.join(report.tickets)}[/dim]")
    for note in report.notes:
        console.print(f"[dim]note: {note}[/dim]")
    if report.total_tokens:
        console.print(f"[dim]{report.total_tokens:,} tokens · {report.model}[/dim]")

"""Rich terminal rendering for scan / apply."""

from __future__ import annotations

from rich.console import Console

from oneport_upgrade.result import UpgradeReport


def render(report: UpgradeReport, console: Console | None = None) -> None:
    console = console or Console()
    c = report.counts()
    console.print()
    console.print(f"[bold]Upgrade {report.migration}[/bold]  "
                 f"[dim]· {report.files_scanned} files scanned[/dim]")

    if not report.findings:
        if report.files_scanned == 0:
            # "Clean" implies we checked and gave the code a pass. With 0 files
            # scanned (e.g. a Python migration target run on a TypeScript repo)
            # nothing was examined — say that, don't imply a clean bill of health.
            console.print("[yellow]Nothing to scan[/yellow] — no Python files found "
                          "for this migration target.")
        else:
            console.print("[bold]✓ Clean[/bold] — no deprecated or removed APIs for this target.")
        return

    # Headline counts
    waived_tail = f", {c['waived']} waived" if c.get("waived") else ""
    console.print(f"\n{c['total']} finding(s): "
                 f"[bold]{c['removed']}[/bold] removed, {c['deprecated']} deprecated  "
                 f"[dim]·[/dim]  {c['auto_fixable']} auto-fixable, {c['manual']} manual{waived_tail}")

    if report.applied:
        console.print(f"\n[bold]Applied[/bold] ({len(report.applied)} fix(es) across "
                     f"{len(report.files_touched)} file(s))")
        for f in report.applied[:20]:
            console.print(f"  [green]✓[/green] {f.file}:{f.line}  {f.title}")

    # Group remaining findings by rule
    remaining = [f for f in report.findings if not f.applied]
    if remaining:
        console.print("\n[bold]To change[/bold]")
        by_rule: dict[str, list] = {}
        for f in remaining:
            by_rule.setdefault(f.rule_id, []).append(f)
        for rid, group in sorted(by_rule.items(), key=lambda kv: (-len(kv[1]))):
            g0 = group[0]
            mark = "[cyan]auto[/cyan]" if g0.auto else "[yellow]manual[/yellow]"
            sev = "[bold]removed[/bold]" if g0.severity == "removed" else "deprecated"
            nwaived = sum(1 for f in group if f.waived)
            if nwaived == len(group):
                wtag = "  [dim][WAIVED][/dim]"
            elif nwaived:
                wtag = f"  [dim]({nwaived} waived)[/dim]"
            else:
                wtag = ""
            console.print(f"  {mark} · {sev} · [bold]{g0.title}[/bold]  [dim]×{len(group)}[/dim]{wtag}")
            console.print(f"      [dim]{group[0].file}:{group[0].line}"
                         + (f" — {g0.hint}" if g0.hint else "") + "[/dim]")

    if report.plan:
        console.print("\n[bold]Migration plan[/bold]")
        for i, step in enumerate(report.plan, 1):
            console.print(f"  {i}. {step}")

    if report.verify and report.verify.ran:
        v = report.verify
        delta = "[green]no regressions[/green]" if not v.regressed else "[bold red]REGRESSED[/bold red]"
        console.print(f"\n[bold]Verify[/bold] (pytest): before {v.before_passed}p/{v.before_failed}f "
                     f"→ after {v.after_passed}p/{v.after_failed}f — {delta}")
    elif report.verify:
        console.print(f"\n[dim]Verify: {report.verify.note}[/dim]")

    for note in report.notes:
        console.print(f"[dim]note: {note}[/dim]")
    if report.total_tokens:
        console.print(f"[dim]{report.total_tokens:,} tokens · {report.model}[/dim]")

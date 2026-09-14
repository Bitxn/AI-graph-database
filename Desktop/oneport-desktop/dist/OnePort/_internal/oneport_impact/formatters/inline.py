"""
Human-facing terminal output.

analyze → the full blast-radius card (callers, tests, co-change, owners, verdict).
check   → the gate summary: findings grouped, plus one line per changed file.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from oneport_impact.result import BlastRadius, ImpactReport, Severity

_SEV_STYLE = {
    Severity.CRITICAL: "bold", Severity.ERROR: "bold",
    Severity.WARNING: "", Severity.INFO: "dim",
}
_SEV_ICON = {Severity.CRITICAL: "■", Severity.ERROR: "■", Severity.WARNING: "○", Severity.INFO: "·"}


def render(report: ImpactReport, console: Console | None = None) -> None:
    console = console or Console()
    if report.mode == "analyze":
        for br in report.blast_radii:
            _render_blast(console, br)
    else:
        _render_check(console, report)
    for note in report.notes:
        console.print(f"[dim]note: {note}[/dim]")
    if report.total_tokens:
        console.print(f"[dim]{report.total_tokens:,} tokens · {report.model}[/dim]")


def _render_blast(console: Console, br: BlastRadius) -> None:
    console.print()
    header = Text()
    header.append("Blast radius: ", style="bold")
    header.append(br.target, style="bold")
    header.append(f"  ({br.target_kind})", style="dim")
    console.print(header)

    verdict = Text("  ")
    if not br.fan_in_available:
        # A non-Python target: don't print "0 call sites" (which reads as "safe,
        # nobody calls it"). State plainly that fan-in wasn't measured.
        verdict.append("FAN-IN N/A", style="bold yellow")
        verdict.append("  ·  call-graph fan-in is Python-only; blast radius below "
                       "is from git co-change + ownership", style="dim")
    else:
        if br.fan_in >= 25:
            verdict.append("LOAD-BEARING", style="bold white on grey30")
        elif br.fan_in >= 8:
            verdict.append("WIDE", style="bold")
        else:
            verdict.append("LOCALISED", style="dim")
        verdict.append(f"  ·  {br.fan_in} call site(s) across {br.caller_files} file(s)", style="dim")
    if br.target_commits:
        verdict.append(f"  ·  {br.target_commits} commits in history", style="dim")
    console.print(verdict)

    if br.verdict:
        console.print(f"\n  [italic]{br.verdict}[/italic]")
        if br.riskiest:
            console.print(f"  [bold]Check first:[/bold] {br.riskiest}"
                         + (f"  [dim]— {br.reason}[/dim]" if br.reason else ""))

    _section(console, "Called from", [
        f"{c.file}:{c.line}  [dim]{c.caller_qualname}[/dim]" for c in br.callers[:10]
    ], empty="no static callers found")
    if len(br.callers) > 10:
        console.print(f"      [dim]… and {len(br.callers) - 10} more call site(s)[/dim]")

    _section(console, "Tests exercising it", [
        f"{t.file}:{t.line}" for t in br.tests[:6]
    ], empty="no tests reference this — changes here are unguarded")

    _section(console, "Changes together with  [dim](temporal coupling)[/dim]", [
        f"{p.pct:>3}%  {p.path}  [dim]({p.together}/{p.target_commits} commits)[/dim]"
        for p in br.partners[:8]
    ], empty="no notable co-change history")

    _section(console, "Ask", [
        (o.name + ("  [dim][CODEOWNER][/dim]" if o.codeowner
                   else f"  [dim]({o.commits} commits, last {o.last_date})[/dim]"))
        for o in br.owners[:4]
    ], empty="no ownership signal")


def _section(console: Console, title: str, rows: list[str], empty: str) -> None:
    console.print(f"\n  [bold]{title}[/bold]")
    if rows:
        for r in rows:
            console.print(f"      {r}")
    else:
        console.print(f"      [dim]{empty}[/dim]")


def _render_check(console: Console, report: ImpactReport) -> None:
    console.print()
    n_block = sum(1 for f in report.findings if f.severity >= Severity.ERROR and not f.waived)
    n_warn = sum(1 for f in report.findings if not (f.severity >= Severity.ERROR) or f.waived)
    # N/A only when we genuinely assessed nothing: no Python to measure fan-in AND
    # no git co-change findings either. If co-change surfaced companions on a
    # non-Python change, that's a real REVIEW, not N/A.
    nothing_analyzed = (report.changed_files > 0 and report.analyzed_files == 0
                        and not report.findings)
    head = Text()
    head.append("Impact check: ", style="bold")
    if report.blocking:
        head.append("BLOCKED", style="bold white on grey30")
    elif nothing_analyzed:
        head.append("N/A", style="bold yellow")
    elif report.findings:
        head.append("REVIEW", style="bold")
    else:
        head.append("CLEAR", style="bold")
    if not nothing_analyzed:
        head.append(f"  — {n_block} blocker(s), {n_warn} advisory", style="dim")
    console.print(head)

    for f in report.findings:
        line = Text("  ")
        line.append(_SEV_ICON[f.severity] + " ", style=_SEV_STYLE[f.severity])
        line.append(f"[{f.rule_id}] ", style="dim")
        line.append(f.message)
        if f.waived:
            line.append("  [WAIVED]", style="dim")
        console.print(line)
        if f.waived and f.waiver_reason:
            console.print(f"      [dim]waived: {f.waiver_reason}[/dim]")
        elif f.fix:
            console.print(f"      [dim]→ {f.fix}[/dim]")
    if not report.findings and not nothing_analyzed:
        console.print("  [dim]No wide-blast-radius changes or missing co-change companions.[/dim]")

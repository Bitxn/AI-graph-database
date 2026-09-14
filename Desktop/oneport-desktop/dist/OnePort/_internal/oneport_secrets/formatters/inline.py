"""
Human-readable terminal output for `scan` and `env-check`. Returns a rich-markup
string (rendered by the CLI). Secrets are always redacted — we never print a full
credential to the terminal or logs.
"""

from __future__ import annotations

from oneport_secrets.result import EnvResult, Finding, ScanResult, Verdict

_SEV_STYLE = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "cyan",
    "info": "dim",
}

_VERDICT_BADGE = {
    Verdict.REAL: "[bold red]REAL[/bold red]",
    Verdict.FALSE_POSITIVE: "[green]false-positive[/green]",
    Verdict.UNREVIEWED: "[yellow]unreviewed[/yellow]",
}


def format_scan(result: ScanResult) -> str:
    lines: list[str] = []
    findings = result.sorted_findings()

    if not findings:
        scope = _scope_line(result)
        lines.append("[bold green]PASS - No secrets detected.[/bold green]")
        lines.append(f"[dim]{scope}[/dim]")
        if result.triage_error:
            lines.append(f"[yellow]LLM triage skipped: {result.triage_error}[/yellow]")
        return "\n".join(lines)

    real = result.real
    fps = result.false_positives
    blocking = result.blocking

    lines.append(f"[bold]Oneport Secrets[/bold] - {_scope_line(result)}")
    lines.append("")

    # Show blocking (real/unreviewed) findings first and in full detail.
    shown = [f for f in findings if f.verdict != Verdict.FALSE_POSITIVE]
    for f in shown:
        lines.extend(_format_finding(f))
        lines.append("")

    if fps:
        lines.append(f"[dim]Suppressed {len(fps)} triaged false-positive(s):[/dim]")
        for f in fps:
            lines.append(f"  [dim]- {f.detector_name} {f.location} : {f.reason or 'placeholder/fixture'}[/dim]")
        lines.append("")

    # Summary
    lines.append(
        f"[bold]Summary:[/bold] {len(real)} real, "
        f"{len([f for f in findings if f.verdict == Verdict.UNREVIEWED])} unreviewed, "
        f"{len(fps)} false-positive"
    )
    if result.triage_error:
        # Say WHY everything is unreviewed, or the user reads a wall of
        # "unreviewed" as the tool being dumb rather than the model being absent.
        lines.append(f"[yellow]LLM triage skipped: {result.triage_error} — "
                     "findings below are UNREVIEWED (placeholders were not filtered out).[/yellow]")
    if blocking:
        lines.append(f"[bold red]FAIL - {len(blocking)} secret(s) must be resolved before shipping.[/bold red]")
    else:
        lines.append("[bold green]PASS - No real secrets, gate passes.[/bold green]")
    return "\n".join(lines)


def _format_finding(f: Finding) -> list[str]:
    style = _SEV_STYLE.get(f.severity, "white")
    badge = _VERDICT_BADGE.get(f.verdict, "")
    out = [
        f"[{style}]* {f.severity.upper()}[/{style}] {badge}  [bold]{f.detector_name}[/bold]",
        f"    location : {f.location}",
        f"    value    : {f.redacted()}",
    ]
    if f.commit:
        who = f" by {f.author}" if f.author else ""
        out.append(f"    commit   : {f.commit[:8]}{who} ({f.date})")
    if f.reason:
        out.append(f"    triage   : {f.reason}")
    if f.entropy is not None:
        out.append(f"    entropy  : {f.entropy:.2f} bits/char")
    if f.remediation:
        out.append("    remediation:")
        for step in f.remediation:
            out.append(f"      - {step}")
    return out


def _scope_line(result: ScanResult) -> str:
    if result.mode == "history":
        return f"scanned {result.scanned_commits} commit(s) + working tree"
    if result.mode == "staged":
        return "scanned staged changes"
    return f"scanned {result.scanned_files} file(s)"


# ── env-check ───────────────────────────────────────────────────────────────────

def format_env(result: EnvResult) -> str:
    lines: list[str] = []
    if not result.has_drift:
        src = result.example_path or "(no .env.example found)"
        lines.append("[bold green]PASS - No env drift.[/bold green]")
        lines.append(f"[dim]Code env reads match {src}.[/dim]")
        return "\n".join(lines)

    if result.used_but_undeclared:
        lines.append("[bold red]Used in code but not declared in .env.example:[/bold red]")
        for v in result.used_but_undeclared:
            tag = _kind_tag(v.kind)
            ref = v.used_in[0] if v.used_in else ""
            more = f" (+{len(v.used_in) - 1} more)" if len(v.used_in) > 1 else ""
            lines.append(f"  [red]x[/red] {v.name} {tag}  [dim]{ref}{more}[/dim]")
        lines.append("")

    if result.declared_but_unused:
        lines.append("[bold yellow]Declared in .env.example but never read in code:[/bold yellow]")
        for v in result.declared_but_unused:
            lines.append(f"  [yellow]-[/yellow] {v.name} {_kind_tag(v.kind)}")
        lines.append("")

    if result.example_path:
        lines.append(f"[dim]Compared against {result.example_path}.[/dim]")
    else:
        lines.append("[dim]No .env.example found — every code env read is reported as undeclared.[/dim]")
    return "\n".join(lines)


def _kind_tag(kind: str) -> str:
    if kind == "secret":
        return "[bold red][SECRET][/bold red]"
    if kind == "toggle":
        return "[cyan][toggle][/cyan]"
    return ""

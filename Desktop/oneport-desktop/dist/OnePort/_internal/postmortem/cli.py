import click
import time
import os
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule

from .analyzer import analyze_incident
from .config import DEFAULT_MODEL, Settings
from .formatter import print_postmortem, save_to_file
from .grounding import annotate, verify_timeline
from .redact import redact_all
from .schema import parse_postmortem
from .slack_parser import parse_slack_export
from .exporter import export_pdf
from .database import save_postmortem, find_similar_incidents, list_postmortems, get_stats
from .jira_client import create_jira_tickets, test_jira_connection
from .setup_wizard import run_setup, load_config_to_env

load_config_to_env()

console = Console()


def _print_pattern_alerts(matches: list[dict]):
    if not matches:
        return
    console.print()
    console.print(Panel(
        f"[bold yellow]⚠  PATTERN DETECTED — This root cause has appeared {len(matches)} time(s) before[/bold yellow]",
        style="yellow",
        expand=False,
    ))
    tbl = Table(show_header=True, header_style="bold yellow", box=None, padding=(0, 2))
    tbl.add_column("ID",       style="dim",        width=4)
    tbl.add_column("Date",     style="white",       width=12)
    tbl.add_column("Title",    style="cyan",        width=35)
    tbl.add_column("Severity", style="bold",        width=10)
    tbl.add_column("Match %",  style="bold yellow", width=8)
    for m in matches:
        color = {"CRITICAL": "red", "HIGH": "yellow", "MEDIUM": "cyan", "LOW": "green"}.get(m["severity"], "white")
        tbl.add_row(
            str(m["id"]), m["date"], m["title"][:35],
            f"[{color}]{m['severity']}[/{color}]", f"{m['score']}%",
        )
    console.print(tbl)
    console.print("  [dim]Check if action items from these incidents were completed.[/dim]\n")


def _print_jira_results(results: list[dict]):
    if not results:
        return
    console.print()
    console.print(Rule("[bold white]JIRA TICKETS CREATED[/bold white]", style="white"))
    console.print()
    tbl = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    tbl.add_column("Priority", style="bold",  width=8)
    tbl.add_column("Ticket",   style="cyan",  width=14)
    tbl.add_column("Action Item",             width=50)
    tbl.add_column("Status",   style="dim",   width=20)
    for r in results:
        color = {"P1": "red", "P2": "yellow", "P3": "cyan"}.get(r["priority"], "white")
        key_display = f"[link={r['url']}]{r['key']}[/link]" if r["key"] else "—"
        status_display = "[green]✓ created[/green]" if r["status"] == "created" else f"[red]{r['status']}[/red]"
        tbl.add_row(
            f"[{color}]{r['priority']}[/{color}]",
            key_display,
            r["description"][:50],
            status_display,
        )
    console.print(tbl)
    console.print()


@click.group()
def cli():
    """oneport-postmortem — AI-powered incident post-mortem generator."""
    pass


@cli.command()
@click.option("--logs", "-l", default=None, type=click.Path(exists=True),
              help="Path to your incident log file (.txt)")
@click.option("--slack-thread", "-s", default=None, type=click.Path(exists=True),
              help="Path to a Slack JSON export of the incident thread")
@click.option("--output", "-o", default=None,
              help="Optional: save post-mortem to a .txt file")
@click.option("--jira", is_flag=True, default=False,
              help="Auto-create Jira tickets for all action items")
@click.option("--no-save", is_flag=True, default=False,
              help="Skip saving to the local pattern-detection database")
@click.option("--format", "-f", "fmt", default="inline",
              type=click.Choice(["inline", "json"], case_sensitive=False),
              help="Output format: 'inline' (pretty) or 'json' (structured, machine-readable)")
@click.option("--no-redact", is_flag=True, default=False,
              help="Do NOT scrub secrets/PII before sending logs to the model (not recommended)")
@click.option("--no-ground", is_flag=True, default=False,
              help="Skip verifying timeline timestamps against the source logs")
@click.option("--model", default=None,
              help=f"Model to use (default: {DEFAULT_MODEL})")
def generate(logs, slack_thread, output, jira, no_save, fmt, no_redact, no_ground, model):
    """Generate a post-mortem from logs, a Slack thread, or both."""

    settings = Settings.load()
    model = model or settings.model
    do_redact = settings.redact and not no_redact
    do_ground = settings.ground and not no_ground
    as_json = fmt.lower() == "json"

    from oneport_account import is_logged_in
    if not is_logged_in():
        console.print("[bold red]Error:[/bold red] Not logged in to Oneport.")
        console.print("  Get a free token at: [cyan]https://oneport.dev[/cyan]")
        console.print("  Then run: [cyan]oneport-account login <token>[/cyan]")
        raise SystemExit(1)

    if not logs and not slack_thread:
        console.print("[bold red]Error:[/bold red] Provide at least one of --logs or --slack-thread.")
        raise SystemExit(1)

    # In JSON mode, stdout must carry ONLY the JSON document — send every human
    # status line to stderr so the output stays pipeable.
    status = Console(stderr=True) if as_json else console

    log_content   = None
    slack_content = None

    if logs:
        with open(logs, "r") as f:
            log_content = f.read()
        status.print(f"\n  [dim]Reading {len(log_content.splitlines())} log lines from {logs}...[/dim]")

    if slack_thread:
        try:
            slack_content = parse_slack_export(slack_thread)
            status.print(f"  [dim]Parsed ~{slack_content.count(chr(10)+'[')} Slack messages...[/dim]")
        except (ValueError, KeyError) as e:
            status.print(f"[bold red]Error parsing Slack file:[/bold red] {e}")
            raise SystemExit(1)

    # Scrub secrets/PII BEFORE anything leaves the machine, and report what was
    # stripped so it's visible, not silent.
    if do_redact:
        (log_content, slack_content), red = redact_all(log_content, slack_content)
        if red.total:
            status.print(f"  [dim]Redacted before sending — {red.summary()}[/dim]")
    else:
        status.print("  [yellow]⚠ redaction disabled — raw logs will be sent to the model[/yellow]")

    if jira:
        status.print("  [dim]Checking Jira connection...[/dim]")
        ok, msg = test_jira_connection()
        if not ok:
            status.print(f"  [bold red]Jira connection failed:[/bold red] {msg}")
            status.print("  [dim]Fix .env and retry, or run without --jira[/dim]")
            raise SystemExit(1)
        status.print(f"  [dim]Jira connected — {msg}[/dim]")

    start_time = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        console=status,
        transient=True,
    ) as progress:
        task = progress.add_task("Analyzing incident data with AI...", total=None)
        raw_postmortem = analyze_incident(
            logs=log_content,
            slack_thread=slack_content,
            model=model,
            redact=False,      # already redacted above (or user opted out)
        )
        progress.update(task, description="Done.")

    elapsed = time.time() - start_time

    # Evidence grounding: verify the model's timeline against the real source.
    source_text = "\n".join(t for t in (log_content, slack_content) if t)
    report = verify_timeline(raw_postmortem, source_text) if do_ground else None
    display_text = annotate(raw_postmortem, report) if report else raw_postmortem

    if as_json:
        pm = parse_postmortem(raw_postmortem)
        if report:
            verified_map = {(c.time, c.event): c.verified for c in report.claims}
            for entry in pm.timeline:
                entry.verified = verified_map.get((entry.time, entry.event))
            pm.unverified_timeline = len(report.unverified)
        click.echo(pm.to_json())
    else:
        print_postmortem(display_text)
        if report and report.note():
            colour = "yellow" if report.unverified or not report.source_had_timestamps else "green"
            status.print(f"  [{colour}]Grounding:[/{colour}] {report.note()}")

    pm_id = None
    if not no_save:
        pm_id = save_postmortem(raw_postmortem, source_file=logs or slack_thread)
        status.print(f"  [dim]Saved to pattern database (ID #{pm_id}) — ~/.oneport/postmortems.db[/dim]")
        similar = find_similar_incidents(raw_postmortem, current_id=pm_id)
        if not as_json:
            _print_pattern_alerts(similar)

    if jira:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            console=status,
            transient=True,
        ) as progress:
            task = progress.add_task("Creating Jira tickets...", total=None)
            jira_results = create_jira_tickets(raw_postmortem, postmortem_id=pm_id)
            progress.update(task, description="Done.")
        if not as_json:
            _print_jira_results(jira_results)

    if output:
        save_to_file(display_text, output)

    status.print(f"  [dim]Generated in {elapsed:.1f} seconds.[/dim]\n")


@cli.command()
@click.argument("input_file", type=click.Path(exists=True))
@click.option("--format", "-f", "fmt", default="pdf",
              type=click.Choice(["pdf"], case_sensitive=False),
              help="Export format (currently: pdf)")
@click.option("--output", "-o", default=None,
              help="Output file path (default: same name as input with .pdf extension)")
def export(input_file, fmt, output):
    """Export a saved post-mortem .txt to PDF."""
    if not output:
        base = os.path.splitext(input_file)[0]
        output = f"{base}.pdf"
    if fmt == "pdf":
        console.print(f"\n  [dim]Generating PDF from {input_file}...[/dim]")
        try:
            export_pdf(input_file, output)
            console.print(f"  [bold green]✓[/bold green] PDF saved to: [cyan]{output}[/cyan]\n")
        except Exception as e:
            console.print(f"[bold red]Error generating PDF:[/bold red] {e}")
            raise SystemExit(1)


@cli.command()
@click.argument("input_file", type=click.Path(exists=True))
def publish(input_file):
    """Publish a saved post-mortem .txt to Confluence."""
    from .confluence_client import publish_to_confluence
    with open(input_file) as f:
        raw = f.read()
    console.print(f"\n  [dim]Publishing to Confluence...[/dim]")
    try:
        result = publish_to_confluence(raw)
        console.print(f"  [bold green]✓[/bold green] Published: [cyan]{result['page_url']}[/cyan]\n")
    except Exception as e:
        console.print(f"  [bold red]✗[/bold red] {e}\n")
        raise SystemExit(1)


@cli.command()
@click.option("--limit", default=20, help="How many records to show (default: 20)")
def history(limit):
    """List all past post-mortems stored in the local database."""
    records = list_postmortems(limit=limit)
    if not records:
        console.print("\n  [dim]No post-mortems yet. Run `postmortem generate` first.[/dim]\n")
        return
    console.print()
    console.print(Rule("[bold white]POST-MORTEM HISTORY[/bold white]", style="white"))
    console.print()
    tbl = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    tbl.add_column("ID",       style="dim",   width=4)
    tbl.add_column("Date",     style="white", width=12)
    tbl.add_column("Title",    style="cyan",  width=40)
    tbl.add_column("Severity", style="bold",  width=10)
    tbl.add_column("Root Cause (truncated)", style="dim", width=50)
    for r in records:
        sev = r.get("severity") or "?"
        color = {"CRITICAL": "red", "HIGH": "yellow", "MEDIUM": "cyan", "LOW": "green"}.get(sev, "white")
        tbl.add_row(
            str(r["id"]),
            (r.get("created_at") or "")[:10],
            (r.get("title") or "Untitled")[:40],
            f"[{color}]{sev}[/{color}]",
            (r.get("root_cause") or "")[:50],
        )
    console.print(tbl)
    console.print()


@cli.command()
def stats():
    """Show aggregate stats and top recurring root causes."""
    data = get_stats()
    console.print()
    console.print(Rule("[bold white]INCIDENT STATISTICS[/bold white]", style="white"))
    console.print()
    console.print(f"  [bold]Total post-mortems:[/bold]  {data['total']}")
    console.print(f"  [bold]Open action items:[/bold]   {data['open_actions']}")
    console.print()
    if data["by_severity"]:
        console.print("  [bold yellow]By Severity[/bold yellow]")
        for row in data["by_severity"]:
            sev = row["severity"] or "Unknown"
            color = {"CRITICAL": "red", "HIGH": "yellow", "MEDIUM": "cyan", "LOW": "green"}.get(sev, "white")
            bar = "█" * row["count"]
            console.print(f"  [{color}]{sev:<10}[/{color}]  {bar}  ({row['count']})")
    console.print()
    if data["top_root_causes"]:
        console.print("  [bold yellow]Top Recurring Root Causes[/bold yellow]")
        for i, row in enumerate(data["top_root_causes"], 1):
            cause = (row["root_cause"] or "Unknown")[:80]
            console.print(f"  [dim]{i}.[/dim] {cause}  [bold yellow]×{row['count']}[/bold yellow]")
    console.print()


@cli.command()
def jira_test():
    """Test your Jira connection and credentials."""
    console.print("\n  [dim]Testing Jira connection...[/dim]")
    ok, msg = test_jira_connection()
    if ok:
        console.print(f"  [bold green]✓[/bold green] {msg}\n")
    else:
        console.print(f"  [bold red]✗[/bold red] {msg}\n")


@cli.command()
@click.option("--reconfigure", is_flag=True, default=False,
              help="Re-run setup even if config already exists.")
def setup(reconfigure):
    """Interactive setup — configure your API keys and integrations."""
    run_setup(reconfigure=reconfigure)


def main():
    cli()
"""
Command-line interface for oneport-evidence.

Entry point: `oneport-evidence`.

  oneport-evidence frameworks                       list frameworks + control maps
  oneport-evidence report --framework soc2          build an evidence pack
        [--period 90d] [--format pdf|html|md|json] [--out FILE] [--org NAME]
        [--source ledger|ingested]
  oneport-evidence ingest report.json               store an op ship report (richer detail)
        (or:  op ship --format json | oneport-evidence ingest -)

Exit codes: 0 ok · 1 error · 2 usage/config/no-data.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import click
from rich.console import Console

from oneport_evidence import __version__
from oneport_evidence.config import load_config
from oneport_evidence.exceptions import ConfigError, EvidenceError, NoData, UnknownFramework


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


_force_utf8()
console = Console(legacy_windows=False)
err = Console(stderr=True, legacy_windows=False)


@click.group()
@click.version_option(version=__version__, prog_name="oneport-evidence")
def main() -> None:
    """Oneport Evidence — SOC 2 / ISO 27001 evidence packs from your op ship history."""


@main.command()
def frameworks():
    """List supported compliance frameworks and their control → gate mappings."""
    from oneport_evidence.frameworks import list_frameworks
    for fw in list_frameworks():
        console.print(f"\n[bold]{fw.id}[/bold] — {fw.name}")
        for c in fw.controls:
            console.print(f"  [cyan]{c.id:<8}[/cyan] {c.name}  "
                         f"[dim]<- {', '.join(c.gates)}[/dim]")


@main.command()
@click.option("--framework", "-F", default="soc2", help="soc2 | iso27001.")
@click.option("--period", "-p", default="90d", help="Reporting window: 30d, 90d, 6m, 1y, or a YYYY-MM-DD start.")
@click.option("--format", "-f", "fmt", type=click.Choice(["pdf", "html", "md", "json"]), default=None)
@click.option("--out", "-o", "out_path", default=None, help="Output file (default: stdout for md/json).")
@click.option("--org", default=None, help="Organization name for the pack header.")
@click.option("--source", type=click.Choice(["ledger", "ingested"]), default="ledger",
              help="Data source: the auto-populated usage ledger, or ingested reports.")
@click.option("--config", "config_path", default=None, help="Path to .oneportrc.")
def report(framework, period, fmt, out_path, org, source, config_path):
    """Build a compliance evidence pack for the period."""
    from oneport_evidence.frameworks import get_framework
    from oneport_evidence.report import build_report, collect_runs

    try:
        config = load_config(config_path=config_path, overrides={"org": org})
        fw = get_framework(framework)
    except (ConfigError, UnknownFramework) as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(2)

    now = int(time.time())
    since = _period_start(period, now)
    runs = collect_runs(since, source)
    rep = build_report(runs, fw, config.org, since, now)

    if not runs:
        err.print(f"[yellow]No control activity found[/yellow] in the last {period} "
                 f"(source: {source}). Run [cyan]op ship[/cyan] a few times first, "
                 "or ingest reports with [cyan]oneport-evidence ingest[/cyan].")
        # Still emit an (empty) pack if an --out/format was requested; else exit 2.
        if not (fmt or out_path):
            sys.exit(2)

    fmt = fmt or (config.output_format if out_path else "md")
    _emit(rep, fmt, out_path, config.classification)


def _emit(rep, fmt, out_path, classification):
    if fmt == "json":
        _write_or_print(rep.to_json(), out_path)
    elif fmt == "md":
        from oneport_evidence.renderers.markdown import render_markdown
        _write_or_print(render_markdown(rep), out_path)
    elif fmt == "html":
        from oneport_evidence.renderers.html import render_html
        out = out_path or "evidence.html"
        Path(out).write_text(render_html(rep, classification), encoding="utf-8")
        console.print(f"[green]Wrote[/green] {out}")
    else:  # pdf
        from oneport_evidence.renderers.pdf import render_pdf
        out = out_path or "evidence.pdf"
        render_pdf(rep, out, classification)
        console.print(f"[green]Wrote[/green] {out}")


def _write_or_print(text, out_path):
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
        console.print(f"[green]Wrote[/green] {out_path}")
    else:
        click.echo(text)


@main.command()
@click.argument("source", default="-")
@click.option("--repo", default="", help="Repo path to fingerprint for this report (optional).")
def ingest(source, repo):
    """Store an `op ship --format json` report for richer evidence.

    SOURCE is a file path, or '-' to read stdin (e.g. `op ship -f json | ... ingest -`).
    """
    from oneport_evidence.store import EvidenceStore, parse_report_json

    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    try:
        data = parse_report_json(raw)
    except Exception as exc:  # noqa: BLE001
        err.print(f"[bold]Error:[/bold] not a valid op ship JSON report: {exc}")
        sys.exit(2)
    store = EvidenceStore()
    # Deliberately not surfacing the row id: it's an AUTOINCREMENT key, so once any
    # row is removed it drifts permanently ahead of the row count ("#4 ... Total: 3"),
    # which reads as an off-by-one bug even though both numbers are correct.
    store.ingest_report(data, repo=repo)
    console.print(f"[green]Ingested[/green] ship event "
                 f"({len(data.get('results', []))} gate result(s)) · "
                 f"{store.event_count()} event(s) stored.")


def _period_start(period: str, now: int) -> int:
    """Resolve a period spec to an epoch-seconds start."""
    period = period.strip().lower()
    m = re.fullmatch(r"(\d+)([dwmy])", period)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit] * n
        return now - days * 86400
    # a date
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            import datetime as _dt
            return int(_dt.datetime.strptime(period, fmt).timestamp())
        except ValueError:
            continue
    return now - 90 * 86400   # default 90d


if __name__ == "__main__":
    main()

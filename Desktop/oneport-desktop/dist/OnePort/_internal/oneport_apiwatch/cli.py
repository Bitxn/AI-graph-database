"""Command-line interface for oneport-apiwatch.

Entry point: `oneport-apiwatch` (registered in pyproject.toml).

Commands:
  oneport-apiwatch check    Probe every endpoint in the checks file
  oneport-apiwatch auth     Show whether a Gemini key is configured (for --explain)

The `check` command is a stateless one-shot: run it from your own cron or a
scheduled GitHub Action. It exits non-zero when any check fails, so a CI/cron
step goes red. Nothing runs on Oneport's servers.
"""

from __future__ import annotations

import sys as _sys


def _force_utf8_stdio() -> None:
    """Windows consoles/pipes default to cp1252, which can't encode the emoji/
    box glyphs in this tool's output — rendering then crashes AFTER the real
    work (sometimes a paid model call) succeeded. Reconfigure to UTF-8 up
    front. Best-effort: a stream that can't reconfigure is left alone."""
    for _stream in (_sys.stdout, _sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


_force_utf8_stdio()

import os
import sys

import click
from rich.console import Console

from oneport_apiwatch import __version__
from oneport_apiwatch.exceptions import AlertError, ApiwatchError, AuthError, ConfigError

console = Console()
err_console = Console(stderr=True)


@click.group()
@click.version_option(__version__, package_name="oneport-apiwatch")
def main() -> None:
    """oneport-apiwatch — self-hosted API health monitoring with AI-explained failures."""


@main.command()
@click.option("--config", "config_path", default=None, help="Path to the checks file.")
@click.option(
    "--alert",
    "alerts_",
    type=click.Choice(["slack", "github-issue"]),
    multiple=True,
    help="On failure, alert this channel (uses your own SLACK_WEBHOOK_URL / GITHUB_TOKEN). "
         "Repeatable: --alert slack --alert github-issue.",
)
@click.option("--explain", is_flag=True,
              help="Add an AI diagnosis to each failure (needs GEMINI_API_KEY).")
@click.option(
    "--format", "-f", "fmt",
    type=click.Choice(["inline", "json", "prometheus"]),
    default="inline",
    help="Output format. 'prometheus' emits gauges for Grafana / a Pushgateway.",
)
@click.option(
    "--no-history", is_flag=True,
    help="Don't read or write the local status-history file (skips trend detection).",
)
def check(
    config_path: str | None,
    alerts_: tuple[str, ...],
    explain: bool,
    fmt: str,
    no_history: bool,
) -> None:
    """Probe every endpoint in the checks file. Exits non-zero if any check fails."""
    from oneport_apiwatch.config import load_config
    from oneport_apiwatch.history import History
    from oneport_apiwatch.probe import run_checks
    from oneport_apiwatch.report import render_inline, to_json

    try:
        config = load_config(config_path=config_path)
    except ConfigError as exc:
        err_console.print(f"[bold red]Config error:[/bold red] {exc}")
        sys.exit(2)

    report = run_checks(config)

    # Load prior history, then apply flap gating / mutes BEFORE recording — the
    # gate (not the raw per-run result) decides the exit code and alerts.
    from oneport_apiwatch.gate import apply_gate
    history: History | None = History(config.history_file) if not no_history else None
    apply_gate(report, config, history)

    if explain and not report.gate_ok:
        from oneport_apiwatch.diagnose import diagnose_report
        diagnose_report(report, config, history)

    # Persist history AFTER diagnosis so the current failure isn't fed to itself.
    if history is not None:
        history.record(report)
        try:
            history.save()
        except OSError as exc:
            err_console.print(f"[yellow]Warning:[/yellow] could not write history: {exc}")

    # Render output
    if fmt == "json":
        click.echo(to_json(report))
    elif fmt == "prometheus":
        from oneport_apiwatch.report import to_prometheus
        click.echo(to_prometheus(report))
    else:
        render_inline(report, console)

    # Alert only on a state CHANGE: a check that just tripped, or one that just
    # recovered. This is the de-dup — no re-alerting every run while still down.
    if alerts_ and (report.newly_failing or report.recovered):
        from oneport_apiwatch.alerts import dispatch
        alert_failed = False
        for channel in alerts_:
            try:
                status = dispatch(report, channel)
                err_console.print(f"[green]Alert sent:[/green] {status}")
            except AlertError as exc:
                err_console.print(f"[bold red]Alert failed ({channel}):[/bold red] {exc}")
                alert_failed = True
        if alert_failed:
            # A failed alert on an already-failing run shouldn't mask the failure,
            # but it IS an operational problem — exit 3 to distinguish it.
            sys.exit(3)

    sys.exit(0 if report.gate_ok else 1)


@main.command()
def auth() -> None:
    """Show whether a Gemini API key is configured (only needed for --explain)."""
    key = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
    if key:
        masked = key[:6] + "..." + key[-4:]
        console.print(f"[green]Gemini key found:[/green] {masked} → {_default_model()}")
    else:
        console.print("[yellow]No Gemini key set.[/yellow] The check works without one.")
        console.print("For --explain, set GEMINI_API_KEY (free at https://aistudio.google.com/apikey).")


def _default_model() -> str:
    from oneport_apiwatch.config import DEFAULT_MODEL
    return os.getenv("APIWATCH_MODEL", DEFAULT_MODEL)


# Surface any uncaught ApiwatchError cleanly rather than as a traceback.
def _run() -> None:  # pragma: no cover - thin wrapper
    try:
        main()
    except ApiwatchError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(2)
    except AuthError as exc:
        err_console.print(f"[bold red]Auth error:[/bold red] {exc}")
        sys.exit(2)


if __name__ == "__main__":  # pragma: no cover
    main()

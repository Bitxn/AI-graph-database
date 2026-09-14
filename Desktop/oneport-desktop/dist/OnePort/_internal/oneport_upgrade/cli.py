"""
Command-line interface for oneport-upgrade.

Entry point: `oneport-upgrade`.

  oneport-upgrade list                       show supported migration targets
  oneport-upgrade scan --to django:5.0 [path]      detect deprecated/removed APIs
  oneport-upgrade plan --to pydantic:2 [path]      scan + an ordered migration plan
  oneport-upgrade apply --to python:3.12 [path]    apply the safe codemods
        [--dry-run] [--verify]                     preview / run tests before+after
  oneport-upgrade auth

Exit codes:
  0  ok
  1  scan --fail-on-removed found removed APIs, OR apply --verify saw a regression
  2  usage / config / unknown-migration / auth error
"""

from __future__ import annotations

import sys

import click
from rich.console import Console

from oneport_upgrade import __version__
from oneport_upgrade.config import load_config
from oneport_upgrade.exceptions import (
    AuthError, ConfigError, UnknownMigration, UpgradeError,
)


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
@click.version_option(version=__version__, prog_name="oneport-upgrade")
def main() -> None:
    """Oneport Upgrade — detect deprecated APIs, apply safe codemods, verify with your tests."""


@main.command("list")
def list_targets():
    """List supported migration targets."""
    from oneport_upgrade.rules import list_migrations
    console.print("\n[bold]Supported migrations[/bold]")
    for m in list_migrations():
        console.print(f"  [cyan]{m.id:<14}[/cyan] {m.title}  [dim]({len(m.rules)} rules)[/dim]")
        console.print(f"    [dim]{m.summary}[/dim]")


@main.command()
@click.option("--to", "target", required=True, help="Migration target, e.g. django:5.0 (see `list`).")
@click.argument("path", default=".")
@click.option("--format", "-f", "fmt", type=click.Choice(["inline", "json", "sarif"]), default=None)
@click.option("--fail-on-removed", is_flag=True, help="Exit 1 if any REMOVED API is used (for CI).")
@click.option("--config", "config_path", default=None, help="Path to .oneportrc.")
def scan(target, path, fmt, fail_on_removed, config_path):
    """Detect deprecated/removed APIs for the target migration (read-only)."""
    report = _run_scan(target, path, config_path, plan=False)
    _render(report, fmt)
    # Waived findings never block, even when they use a removed API.
    if fail_on_removed and report.blocking_removed:
        sys.exit(1)


@main.command()
@click.option("--to", "target", required=True, help="Migration target, e.g. pydantic:2.")
@click.argument("path", default=".")
@click.option("--format", "-f", "fmt", type=click.Choice(["inline", "json", "sarif"]), default=None)
@click.option("--config", "config_path", default=None, help="Path to .oneportrc.")
def plan(target, path, fmt, config_path):
    """Scan and produce an ordered, written migration plan (needs a model key)."""
    report = _run_scan(target, path, config_path, plan=True)
    _render(report, fmt)


@main.command()
@click.option("--to", "target", required=True, help="Migration target, e.g. python:3.12.")
@click.argument("path", default=".")
@click.option("--dry-run", is_flag=True, help="Show what would change; write nothing.")
@click.option("--verify", "do_verify", is_flag=True, help="Run pytest before and after the fixes.")
@click.option("--format", "-f", "fmt", type=click.Choice(["inline", "json", "sarif"]), default=None)
@click.option("--config", "config_path", default=None, help="Path to .oneportrc.")
def apply(target, path, dry_run, do_verify, fmt, config_path):
    """Apply the safe (auto) codemods to the working tree."""
    from oneport_upgrade.upgrader import Upgrader
    try:
        config = load_config(config_path=config_path)
        report = Upgrader(config, root=path).apply(target, dry_run=dry_run, do_verify=do_verify)
    except UnknownMigration as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(2)
    except (ConfigError, AuthError) as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(2)
    except UpgradeError as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(1)
    _render(report, fmt or config.output_format)
    if report.verify and report.verify.regressed:
        err.print("[bold red]Tests regressed after the codemods — review before committing.[/bold red]")
        sys.exit(1)


@main.command()
def auth():
    """Show your Oneport login and token balance (used only for `plan`)."""
    from oneport_account import fetch_balance, is_logged_in

    if not is_logged_in():
        console.print("[yellow]Not logged in to Oneport.[/yellow] scan and apply need no "
                      "login; only the written `plan` does.")
        console.print("  Run: [bold]oneport-account login <token>[/bold]  "
                      "(free token at https://oneport.dev)")
        return
    try:
        bal = fetch_balance()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Logged in, but couldn't fetch balance:[/yellow] {exc}")
        return
    console.print(f"[green]Logged in:[/green] {bal.get('email', '?')} "
                  f"[dim]({bal.get('tier', 'free')})[/dim]")
    console.print(f"  Balance: {bal.get('balance', 0):,} tokens -> gemini-flash-latest (plan)")


def _run_scan(target, path, config_path, plan):
    from oneport_upgrade.upgrader import Upgrader
    try:
        config = load_config(config_path=config_path)
        return Upgrader(config, root=path).scan(target, plan=plan)
    except UnknownMigration as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(2)
    except (ConfigError, AuthError) as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(2)
    except UpgradeError as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(1)


def _render(report, fmt):
    fmt = fmt or "inline"
    if fmt == "json":
        from oneport_upgrade.formatters.json_fmt import format_json
        click.echo(format_json(report))
    elif fmt == "sarif":
        from oneport_upgrade.formatters.sarif import format_sarif
        click.echo(format_sarif(report))
    else:
        from oneport_upgrade.formatters.inline import render
        render(report, console)


if __name__ == "__main__":
    main()

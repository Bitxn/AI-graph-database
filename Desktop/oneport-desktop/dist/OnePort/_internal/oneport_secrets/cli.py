"""
Command-line interface for Oneport Secrets.

Entry point: `oneport-secrets` (console script in pyproject.toml).

  oneport-secrets scan [PATH] [--staged|--history] [--post] [--format inline|json]
  oneport-secrets env-check [PATH] [--format inline|json]
  oneport-secrets learn "regex: MYCORP_[A-Z0-9]{32}"
  oneport-secrets auth
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

from oneport_secrets import __version__
from oneport_secrets.config import load_config
from oneport_secrets.exceptions import (
    AuthError,
    ConfigError,
    IntegrationError,
    OneportError,
    ScanError,
)

console = Console()
err = Console(stderr=True)


@click.group()
@click.version_option(__version__, package_name="oneport-secrets")
def main() -> None:
    """Oneport Secrets — deterministic secret detection, LLM triage, git-history aware."""


# ── scan ────────────────────────────────────────────────────────────────────────

@main.command()
@click.argument("path", default=".")
@click.option("--staged", is_flag=True, help="Scan only staged changes (pre-commit gate).")
@click.option("--head", "head", is_flag=True, help="Scan only lines added in the last commit (pre-ship gate).")
@click.option("--history", is_flag=True, help="Scan the full git history (+ working tree).")
@click.option("--no-triage", is_flag=True, help="Skip LLM triage; report every deterministic hit.")
@click.option("--no-entropy", is_flag=True, help="Disable Shannon-entropy detection.")
@click.option("--post", "do_post", is_flag=True, help="Post findings to a GitHub PR (needs GITHUB_TOKEN).")
@click.option("--pr", "pr_url", default=None, help="GitHub PR URL for --post (else inferred from CI env).")
@click.option("--format", "-f", "fmt", type=click.Choice(["inline", "json"]), default=None)
@click.option("--config", "config_path", default=None, help="Path to .oneportrc.")
def scan(
    path: str,
    staged: bool,
    head: bool,
    history: bool,
    no_triage: bool,
    no_entropy: bool,
    do_post: bool,
    pr_url: str | None,
    fmt: str | None,
    config_path: str | None,
) -> None:
    """Scan PATH for committed secrets. Exit 1 if any REAL (or untriaged) secret is found."""
    from oneport_secrets.pipeline import post_to_github, run_scan

    if sum([staged, head, history]) > 1:
        err.print("[bold red]Error:[/bold red] use only one of --staged / --head / --history.")
        sys.exit(2)
    mode = "staged" if staged else "head" if head else "history" if history else "worktree"

    try:
        config = load_config(config_path=config_path, overrides={"entropy": not no_entropy})
        # A history scan walks every commit and can run for a while on a big repo.
        # Show a live spinner (on stderr, so --format json stays clean) so the user
        # knows it's working instead of assuming it hung.
        show_spinner = mode == "history" and (fmt or config.output_format) != "json" and err.is_terminal
        if show_spinner:
            with err.status(f"[cyan]Scanning git history…[/cyan]", spinner="dots") as status:
                def _on_commit(n: int) -> None:
                    status.update(f"[cyan]Scanning git history…[/cyan] [dim]{n:,} commits[/dim]")
                result = run_scan(path, mode, config, triage=not no_triage, on_commit=_on_commit)
        else:
            result = run_scan(path, mode, config, triage=not no_triage)
    except ScanError as exc:
        err.print(f"[bold red]Scan error:[/bold red] {exc}")
        sys.exit(2)
    except AuthError as exc:
        err.print(f"[bold red]Auth error:[/bold red] {exc}")
        sys.exit(2)
    except ConfigError as exc:
        err.print(f"[bold red]Config error:[/bold red] {exc}")
        sys.exit(2)
    except OneportError as exc:
        err.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)

    _render_scan(result, fmt or config.output_format)

    if do_post:
        try:
            posted = post_to_github(result, config, pr_url)
            err.print(f"[green]Posted to GitHub:[/green] {posted.get('html_url', '')}")
        except (IntegrationError, OneportError) as exc:
            err.print(f"[bold red]Failed to post:[/bold red] {exc}")
            sys.exit(2)

    if result.has_blocking_secrets:
        sys.exit(1)


def _render_scan(result, fmt: str) -> None:
    if fmt == "json":
        from oneport_secrets.formatters.json_fmt import format_scan
        click.echo(format_scan(result))
    else:
        from oneport_secrets.formatters.inline import format_scan
        console.print(format_scan(result))


# ── env-check ─────────────────────────────────────────────────────────────────

@main.command("env-check")
@click.argument("path", default=".")
@click.option("--no-classify", is_flag=True, help="Skip LLM secret-vs-toggle labelling.")
@click.option("--strict", is_flag=True, help="Exit 1 on any drift (default: only on undeclared vars).")
@click.option("--format", "-f", "fmt", type=click.Choice(["inline", "json"]), default=None)
@click.option("--config", "config_path", default=None, help="Path to .oneportrc.")
def env_check(path: str, no_classify: bool, strict: bool, fmt: str | None, config_path: str | None) -> None:
    """Diff code env reads against .env.example. Exit 1 on used-but-undeclared vars."""
    from oneport_secrets.envcheck import EnvChecker
    from oneport_secrets.guidelines import load_guidelines

    try:
        config = load_config(config_path=config_path)
    except ConfigError:
        config = None  # env-check works fine with no key (just no classification)

    guidelines_path = config.guidelines_path if config else ".oneport/guidelines.md"
    ignore = (config.ignore_paths if config else []) + load_guidelines(guidelines_path).ignore_paths

    result = EnvChecker(config=config).check(path, ignore=ignore, classify=not no_classify)

    if (fmt or (config.output_format if config else "inline")) == "json":
        from oneport_secrets.formatters.json_fmt import format_env
        click.echo(format_env(result))
    else:
        from oneport_secrets.formatters.inline import format_env
        console.print(format_env(result))

    fail = result.has_drift if strict else bool(result.used_but_undeclared)
    if fail:
        sys.exit(1)


# ── learn ──────────────────────────────────────────────────────────────────────

@main.command()
@click.argument("directive")
@click.option("--path", "guidelines_path", default=None, help="Guidelines file (default .oneport/guidelines.md).")
def learn(directive: str, guidelines_path: str | None) -> None:
    """Add a scanner directive, e.g.  learn "regex: MYCORP_[A-Z0-9]{32}"  or  learn "ignore: tests/"."""
    from oneport_secrets.guidelines import DEFAULT_GUIDELINES_PATH, append_guideline

    path = guidelines_path or DEFAULT_GUIDELINES_PATH
    try:
        written = append_guideline(directive, path=path)
    except ValueError as exc:
        err.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(2)
    console.print(f"[green]Learned.[/green] Added to [bold]{written}[/bold]:  {directive}")
    console.print("[dim]Commit the file so the whole team's scans pick it up.[/dim]")


# ── auth ───────────────────────────────────────────────────────────────────────

@main.command()
def auth() -> None:
    """Show your Oneport login and token balance (used only for triage)."""
    from oneport_account import fetch_balance, is_logged_in

    if not is_logged_in():
        console.print("[yellow]Not logged in to Oneport.[/yellow] Detection still works; "
                      "triage is skipped.")
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
    console.print(f"  Balance: {bal.get('balance', 0):,} tokens -> gemini-flash-latest (triage)")


if __name__ == "__main__":
    main()

"""
Command-line interface for oneport-impact.

Entry point: `oneport-impact` (console script in pyproject.toml).

  oneport-impact analyze <symbol|file> [--format inline|json] [--no-llm]
  oneport-impact check --staged|--head    [--format inline|json] [--fail-on ...]
  oneport-impact auth

Exit codes (CI contract, shared across Oneport tools):
  0  clear (or analyze, which is informational)
  1  blocking findings in `check` (per --fail-on)
  2  usage / config / auth / git error
"""

from __future__ import annotations

import sys

import click
from rich.console import Console

from oneport_impact import __version__
from oneport_impact.config import load_config
from oneport_impact.exceptions import (
    AuthError, ConfigError, GitError, ImpactError, ResolveError,
)
from oneport_impact.result import Severity


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
@click.version_option(version=__version__, prog_name="oneport-impact")
def main() -> None:
    """Oneport Impact — what breaks if I touch this? Call graph + git co-change, judged by an LLM."""


# ── analyze ──────────────────────────────────────────────────────────────────

@main.command()
@click.argument("query")
@click.option("--repo", default=".", help="Repository root (default: current directory).")
@click.option("--format", "-f", "fmt", type=click.Choice(["inline", "json", "sarif"]), default=None)
@click.option("--no-llm", is_flag=True, help="Facts only; skip the risk-verdict layer.")
@click.option("--config", "config_path", default=None, help="Path to .oneportrc.")
def analyze(query, repo, fmt, no_llm, config_path):
    """Show the blast radius of a symbol or file.

    QUERY can be a function/class name ('charge'), a dotted qualname
    ('billing.stripe.Client.charge'), or a repo-relative path ('billing/stripe.py').
    """
    from oneport_impact.analyzer import Analyzer

    try:
        config = load_config(config_path=config_path)
        report = Analyzer(config, root=repo).analyze(query, use_llm=not no_llm)
    except ResolveError as exc:
        err.print(f"[bold]Not found:[/bold] {exc}")
        sys.exit(2)
    except (ConfigError, GitError, AuthError) as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(2)
    except ImpactError as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(1)

    _render(report, fmt or config.output_format)
    # analyze is informational — always exit 0.


# ── check (gate) ─────────────────────────────────────────────────────────────

@main.command()
@click.argument("target", required=False)
@click.option("--staged", "use_staged", is_flag=True, help="Check staged changes (default).")
@click.option("--head", "use_head", is_flag=True, help="Check the last commit (HEAD~1..HEAD).")
@click.option("--repo", default=None, help="Repository root (default: current directory or TARGET).")
@click.option("--format", "-f", "fmt", type=click.Choice(["inline", "json", "sarif"]), default=None)
@click.option("--fail-on", type=click.Choice(["info", "warning", "error", "critical"]),
              default="error", show_default=True, help="Severity that blocks (exit 1).")
@click.option("--no-llm", is_flag=True, help="Skip the risk-verdict layer.")
@click.option("--config", "config_path", default=None, help="Path to .oneportrc.")
def check(target, use_staged, use_head, repo, fmt, fail_on, no_llm, config_path):
    """Gate a change set: flag wide-blast-radius edits and missing co-change companions.

    TARGET is an optional repository path (so `op ship` can pass it); the change
    set itself comes from --staged (default) or --head.
    """
    from oneport_impact.analyzer import Analyzer

    # A positional TARGET (e.g. "." from op ship) is the repo root, not a change set.
    repo = repo or (target if target else ".")
    mode = "head" if use_head else "staged"
    try:
        config = load_config(config_path=config_path)
        report = Analyzer(config, root=repo).check(
            mode, use_llm=not no_llm, fail_on=Severity(fail_on))
    except GitError as exc:
        err.print(f"[bold]Git error:[/bold] {exc}")
        sys.exit(2)
    except (ConfigError, AuthError) as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(2)
    except ImpactError as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(1)

    _render(report, fmt or config.output_format)
    if report.has_blocking:
        sys.exit(1)


# ── auth ─────────────────────────────────────────────────────────────────────

@main.command()
def auth():
    """Show your Oneport login and token balance (used for the risk verdict)."""
    from oneport_account import fetch_balance, is_logged_in

    if not is_logged_in():
        console.print("[yellow]Not logged in to Oneport.[/yellow] The call graph, co-change, "
                      "and ownership all work without it; only the risk verdict needs it.")
        console.print("  Run: [bold]oneport-account login <token>[/bold]  "
                      "(free token at https://oneport.dev)")
        return
    try:
        bal = fetch_balance()
    except Exception as exc:  # noqa: BLE001 - surface any lookup issue plainly
        console.print(f"[yellow]Logged in, but couldn't fetch balance:[/yellow] {exc}")
        return
    console.print(f"[green]Logged in:[/green] {bal.get('email', '?')} "
                  f"[dim]({bal.get('tier', 'free')})[/dim]")
    console.print(f"  Balance: {bal.get('balance', 0):,} tokens -> gemini-flash-latest")


def _render(report, fmt: str) -> None:
    if fmt == "json":
        from oneport_impact.formatters.json_fmt import format_json
        click.echo(format_json(report))
    elif fmt == "sarif":
        from oneport_impact.formatters.sarif_fmt import format_sarif
        click.echo(format_sarif(report))
    else:
        from oneport_impact.formatters.inline import render
        render(report, console)


if __name__ == "__main__":
    main()

"""
Command-line interface for oneport-standup.

Entry point: `oneport-standup`.

  oneport-standup daily              what you did in the last 24h → standup notes
  oneport-standup weekly             the last 7 days → weekly summary
  oneport-standup since <ref|date>   an arbitrary window
  oneport-standup release <a> [b]    release notes for a..b (b defaults to HEAD)
  oneport-standup auth

All commands default to YOUR commits (git user.email); --all or --author widen it.
Exit 0 always — this is informational (2 on a git/config error).
"""

from __future__ import annotations

import sys

import click
from rich.console import Console

from oneport_standup import __version__
from oneport_standup.config import load_config
from oneport_standup.exceptions import (
    AuthError, ConfigError, GitError, NothingToReport, StandupError,
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

# Options shared by the reporting commands.
_common = [
    click.option("--repo", default=".", help="Repository (default: current directory)."),
    click.option("--author", default=None, help="Filter to this author (name or email)."),
    click.option("--all", "all_authors", is_flag=True, help="Everyone's commits, not just yours."),
    click.option("--format", "-f", "fmt", type=click.Choice(["markdown", "inline", "json"]), default=None),
    click.option("--no-llm", is_flag=True, help="Skip narration; group commits deterministically."),
    click.option("--post", "post_url", default=None, metavar="SLACK_WEBHOOK",
                 help="Also post the report to a Slack incoming-webhook URL."),
    click.option("--config", "config_path", default=None, help="Path to .oneportrc."),
]


def common_options(func):
    for opt in reversed(_common):
        func = opt(func)
    return func


@click.group()
@click.version_option(version=__version__, prog_name="oneport-standup")
def main() -> None:
    """Oneport Standup — your git history as standup notes, summaries, and release notes."""


def _run(mode, repo, author, all_authors, fmt, no_llm, config_path,
         post_url=None, value=None, to_ref=None):
    from oneport_standup.ranges import author_for, window_for
    from oneport_standup.reporter import build_report
    from oneport_standup.integrations.local_git import require_repo

    try:
        require_repo(repo)
        config = load_config(config_path=config_path)
        window = window_for(mode, value=value, to_ref=to_ref)
        who = author_for(repo, all_authors, author)
        report = build_report(config, repo, mode, window, who, use_llm=not no_llm)
    except NothingToReport as exc:
        console.print(f"[dim]{exc}[/dim]")
        return
    except GitError as exc:
        err.print(f"[bold]Git error:[/bold] {exc}")
        sys.exit(2)
    except (ConfigError, AuthError) as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(2)
    except StandupError as exc:
        err.print(f"[bold]Error:[/bold] {exc}")
        sys.exit(1)

    _render(report, fmt or config.output_format)

    if post_url:
        from oneport_standup.exceptions import PostError
        from oneport_standup.integrations.slack import post_to_slack
        try:
            post_to_slack(report, post_url)
            err.print("[green]Posted to Slack.[/green]")
        except PostError as exc:
            err.print(f"[bold]Failed to post to Slack:[/bold] {exc}")
            sys.exit(2)


def _render(report, fmt: str) -> None:
    if fmt == "json":
        from oneport_standup.formatters.json_fmt import format_json
        click.echo(format_json(report))
    elif fmt == "inline":
        from oneport_standup.formatters.inline import render
        render(report, console)
    else:
        from oneport_standup.formatters.markdown import format_markdown
        click.echo(format_markdown(report))


@main.command()
@common_options
def daily(repo, author, all_authors, fmt, no_llm, post_url, config_path):
    """Standup notes from the last 24 hours."""
    _run("daily", repo, author, all_authors, fmt, no_llm, config_path, post_url=post_url)


@main.command()
@common_options
def weekly(repo, author, all_authors, fmt, no_llm, post_url, config_path):
    """A weekly summary from the last 7 days."""
    _run("weekly", repo, author, all_authors, fmt, no_llm, config_path, post_url=post_url)


@main.command()
@click.argument("ref")
@common_options
def since(ref, repo, author, all_authors, fmt, no_llm, post_url, config_path):
    """Summarize work since REF (a git ref, or a date like 2026-07-01 / '3 days ago')."""
    _run("since", repo, author, all_authors, fmt, no_llm, config_path, post_url=post_url, value=ref)


@main.command()
@click.argument("from_ref")
@click.argument("to_ref", required=False, default="HEAD")
@common_options
def release(from_ref, to_ref, repo, author, all_authors, fmt, no_llm, post_url, config_path):
    """Release notes for the range FROM_REF..TO_REF (TO_REF defaults to HEAD)."""
    # Release notes describe the whole release, so default to everyone's work.
    _run("release", repo, author, True if not author else all_authors, fmt, no_llm,
         config_path, post_url=post_url, value=from_ref, to_ref=to_ref)


@main.command()
def auth():
    """Show your Oneport login and token balance."""
    from oneport_account import fetch_balance, is_logged_in

    if not is_logged_in():
        console.print("[yellow]Not logged in to Oneport.[/yellow] Summaries still work "
                      "(commits grouped by day); logging in adds the written narrative.")
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


if __name__ == "__main__":
    main()

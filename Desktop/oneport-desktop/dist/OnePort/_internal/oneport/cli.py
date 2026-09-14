"""
Command-line interface for Oneport Review.

Entry point: `oneport-review` (console script), `op review` (via the umbrella),
or `oneport review` (umbrella passthrough).

Commands:
  oneport-review review <target>    Review a file, PR URL, or diff
  oneport-review rules list         List all available rule IDs
  oneport-review cache clear        Clear the local review cache
  oneport-review auth               Set or check the API key
"""

from __future__ import annotations

import sys
import os


def _force_utf8_stdio() -> None:
    """Windows consoles/pipes default to cp1252, which can't encode the 🟠/🔴
    severity glyphs in review output — rendering then crashes AFTER a paid
    model call succeeded. Reconfigure to UTF-8 up front. Best-effort."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


_force_utf8_stdio()

import click
from rich.console import Console

from oneport.config import load_config
from oneport.exceptions import OneportError, AuthError, ConfigError, IntegrationError
from oneport.reviewer import Reviewer
from oneport.result import Severity

console = Console()
err_console = Console(stderr=True)


# ── Root group ─────────────────────────────────────────────────────────────────

@click.group()
@click.version_option(package_name="oneport-review")
def main() -> None:
    """Oneport Review — AI-powered code review for teams without a senior engineer."""


# ── review command ─────────────────────────────────────────────────────────────

@main.command()
# Optional: --staged / --head derive the change set from git and need no path.
# Requiring it made `oneport review --staged` fail with "Missing argument TARGET".
@click.argument("target", required=False)
@click.option(
    "--format", "-f",
    type=click.Choice(["inline", "json", "github", "sarif"]),
    default=None,
    help="Output format. Overrides .oneportrc.",
)
@click.option(
    "--min-severity", "-s",
    type=click.Choice(["info", "warning", "error", "critical"]),
    default=None,
    help="Minimum severity to DISPLAY. Never affects the exit code.",
)
@click.option(
    "--fail-on",
    type=click.Choice(["info", "warning", "error", "critical"]),
    default=None,
    help="Severity that BLOCKS CI (exit 1). Default: error. Waived findings never block.",
)
@click.option("--config", "config_path", default=None, help="Path to .oneportrc file.")
@click.option("--no-cache", is_flag=True, help="Skip cache and force a fresh review.")
@click.option("--staged", "use_staged", is_flag=True, help="Review staged changes (git diff --cached).")
@click.option("--head", "use_head", is_flag=True, help="Review last commit (git diff HEAD~1).")
@click.option(
    "--post", "post_review", is_flag=True,
    help="Post findings as an inline GitHub PR review (requires GITHUB_TOKEN). "
         "Only valid when TARGET is a GitHub PR URL.",
)
@click.option(
    "--full", "force_full", is_flag=True,
    help="Force a full review of the whole PR, even if Oneport reviewed an "
         "earlier revision (default: incremental — only new commits).",
)
def review(
    target: str | None,
    format: str | None,
    min_severity: str | None,
    fail_on: str | None,
    config_path: str | None,
    no_cache: bool,
    use_staged: bool,
    use_head: bool,
    post_review: bool,
    force_full: bool,
) -> None:
    """Review a file, PR URL, or diff.

    TARGET can be:
      - A file path:                  src/auth.py
      - A GitHub PR URL:              https://github.com/org/repo/pull/42
      - A GitLab MR URL:              https://gitlab.com/org/repo/-/merge_requests/7
      - A Bitbucket PR URL:           https://bitbucket.org/org/repo/pull-requests/3
    """
    if use_staged:
        target = "--staged"
    elif use_head:
        target = "--head"
    elif not target:
        raise click.UsageError(
            "Provide a TARGET (a file path or PR URL), or use --staged / --head "
            "to review your current change set."
        )

    try:
        overrides: dict = {}
        if format:
            overrides.setdefault("output", {})["format"] = format
        if fail_on:
            overrides["fail_on"] = fail_on
        if no_cache:
            overrides.setdefault("cache", {})["enabled"] = False

        config = load_config(config_path=config_path, overrides=overrides if overrides else None)
        reviewer = Reviewer(config=config)
        effective_severity = min_severity or config.output.min_severity
        result = reviewer.review(target, min_severity=effective_severity, full=force_full)

    except AuthError as exc:
        # The AuthError itself carries the correct managed-proxy guidance ("log in"
        # / "top up / redeem"). Don't append BYOK key advice — this tool holds no
        # model key; the Oneport proxy does.
        err_console.print(f"[bold red]Auth error:[/bold red] {exc}")
        sys.exit(2)
    except ConfigError as exc:
        err_console.print(f"[bold red]Config error:[/bold red] {exc}")
        sys.exit(2)
    except FileNotFoundError as exc:
        err_console.print(f"[bold red]File not found:[/bold red] {exc}")
        sys.exit(2)
    except OneportError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)

    # Render output
    effective_format = format or config.output.format
    _render(result, effective_format)

    # Post inline PR review, if requested
    if post_review:
        if not result.pr_ref:
            err_console.print(
                "[bold red]Error:[/bold red] --post only works when TARGET is a GitHub PR URL."
            )
            sys.exit(2)
        try:
            posted = reviewer.post_github_review(result)
            # stderr, not stdout — keeps `--format json`/sarif output machine-parseable
            err_console.print(f"[green]Posted review to GitHub:[/green] {posted.get('html_url', '')}")
        except IntegrationError as exc:
            err_console.print(f"[bold red]Failed to post review:[/bold red] {exc}")
            sys.exit(2)

    # Exit 1 if blocking issues exist (for CI)
    if result.has_blocking_issues:
        sys.exit(1)


def _render(result, fmt: str) -> None:
    from oneport.formatters.inline import InlineFormatter
    from oneport.formatters.json_fmt import JsonFormatter
    from oneport.formatters.github_fmt import GitHubFormatter
    from oneport.formatters.sarif import SarifFormatter

    formatter_map = {
        "inline": InlineFormatter,
        "json": JsonFormatter,
        "github": GitHubFormatter,
        "sarif": SarifFormatter,
    }
    formatter_cls = formatter_map.get(fmt, InlineFormatter)
    output = formatter_cls().format(result)
    # Inline output already carries its own ANSI styling — echo it raw.
    # Feeding it back through rich would re-interpret [CRITICAL] as markup.
    click.echo(output)


# ── summarize command ──────────────────────────────────────────────────────────

@main.command()
@click.argument("pr_url")
@click.option("--post", "post_summary", is_flag=True,
              help="Upsert the summary as a sticky comment on the PR (requires GITHUB_TOKEN).")
@click.option("--format", "-f", "fmt", type=click.Choice(["markdown", "json"]),
              default="markdown", help="Output format for stdout.")
@click.option("--config", "config_path", default=None, help="Path to .oneportrc file.")
def summarize(pr_url: str, post_summary: bool, fmt: str, config_path: str | None) -> None:
    """Generate a PR briefing: summary, per-file walkthrough, and Mermaid diagram.

    PR_URL must be a GitHub PR URL, e.g. https://github.com/org/repo/pull/42
    """
    from oneport.summarizer import Summarizer, render_markdown

    try:
        config = load_config(config_path=config_path)
        summarizer = Summarizer(config=config)
        summary = summarizer.summarize(pr_url)
    except AuthError as exc:
        # Managed proxy holds the key — the AuthError already says log in / top up.
        err_console.print(f"[bold red]Auth error:[/bold red] {exc}")
        sys.exit(2)
    except OneportError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)

    if fmt == "json":
        import json as _json
        click.echo(_json.dumps(summary.to_dict(), indent=2))
    else:
        click.echo(render_markdown(summary))

    if post_summary:
        try:
            posted = summarizer.post(summary)
            err_console.print(
                f"[green]Posted summary to GitHub:[/green] {posted.get('html_url', '')}"
            )
        except IntegrationError as exc:
            err_console.print(f"[bold red]Failed to post summary:[/bold red] {exc}")
            sys.exit(2)


# ── respond command (interactive PR chat) ─────────────────────────────────────

@main.command()
@click.option("--event-path", default=None,
              help="Path to the GitHub event payload JSON. "
                   "Defaults to $GITHUB_EVENT_PATH (set automatically in Actions).")
@click.option("--trigger", default="@oneport", show_default=True,
              help="Mention that activates a reply.")
@click.option("--config", "config_path", default=None, help="Path to .oneportrc file.")
def respond(event_path: str | None, trigger: str, config_path: str | None) -> None:
    """Answer an @oneport mention in a PR thread (run from GitHub Actions).

    Reads the triggering issue_comment / pull_request_review_comment event,
    and replies in-thread. Supports "@oneport remember: <rule>" to commit a
    team guideline to the PR branch.

    Exits 0 (without calling the model) when the comment isn't addressed to
    Oneport, so the workflow stays green on ordinary human comments.
    """
    from oneport.chat import Responder, load_event_file, parse_event

    event_path = event_path or os.getenv("GITHUB_EVENT_PATH", "")
    if not event_path:
        err_console.print(
            "[bold red]Error:[/bold red] no event payload. Pass --event-path or "
            "run inside GitHub Actions (GITHUB_EVENT_PATH)."
        )
        sys.exit(2)

    try:
        event = parse_event(load_event_file(event_path), trigger=trigger)
    except (OSError, ValueError) as exc:
        err_console.print(f"[bold red]Error reading event payload:[/bold red] {exc}")
        sys.exit(2)

    if event is None:
        console.print("[dim]Comment is not addressed to Oneport — nothing to do.[/dim]")
        return

    try:
        config = load_config(config_path=config_path)
        posted = Responder(config=config).respond(event, trigger=trigger)
    except AuthError as exc:
        err_console.print(f"[bold red]Auth error:[/bold red] {exc}")
        sys.exit(2)
    except OneportError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)

    console.print(f"[green]Replied:[/green] {posted.get('html_url', '')}")


# ── learn command ──────────────────────────────────────────────────────────────

@main.command()
@click.argument("guideline")
@click.option("--path", "guidelines_path", default=None,
              help="Guidelines file path. Defaults to .oneport/guidelines.md "
                   "(or guidelines_path in .oneportrc).")
def learn(guideline: str, guidelines_path: str | None) -> None:
    """Teach Oneport a team-specific review rule.

    The guideline is appended to the repo's guidelines file and enforced on
    every future review, exactly like a built-in rule.

    Example:  oneport learn "never use print() in library code, use logging"
    """
    from oneport.guidelines import append_guideline, DEFAULT_GUIDELINES_PATH

    path = guidelines_path
    if path is None:
        # Config is optional here — learning shouldn't require an API key.
        try:
            path = load_config().guidelines_path
        except OneportError:
            path = DEFAULT_GUIDELINES_PATH

    try:
        written = append_guideline(guideline, path=path)
    except ValueError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(2)

    console.print(f"[green]Learned.[/green] Added to [bold]{written}[/bold]:")
    console.print(f"  {guideline}")
    console.print("[dim]Commit the file so the whole team's reviews enforce it.[/dim]")


# ── rules command ──────────────────────────────────────────────────────────────

@main.group()
def rules() -> None:
    """Manage review rules."""


@rules.command("list")
@click.option("--category", "-c", default=None, help="Filter by category.")
def rules_list(category: str | None) -> None:
    """List all available rule IDs and descriptions."""
    from oneport.rules.loader import load_rule_set
    rule_set = load_rule_set()
    for rule in rule_set.all_rules:
        if category and rule.category != category:
            continue
        console.print(f"[bold]{rule.id}[/bold]  [{rule.severity}] {rule.description}")


# ── install-hooks command ──────────────────────────────────────────────────────

@main.command("install-hooks")
@click.option("--force", is_flag=True,
              help="Replace an existing non-Oneport pre-commit hook (backs it up to pre-commit.bak).")
def install_hooks(force: bool) -> None:
    """Install a git pre-commit hook that reviews staged changes.

    Commits with error/critical findings are blocked. Bypass once with
    ONEPORT_SKIP=1. Prefer the pre-commit framework? See .pre-commit-hooks.yaml
    in the docs instead.
    """
    from oneport.hooks import find_git_dir, install_pre_commit_hook

    git_dir = find_git_dir()
    if git_dir is None:
        err_console.print("[bold red]Error:[/bold red] not inside a git repository.")
        sys.exit(2)

    try:
        hook_path = install_pre_commit_hook(git_dir, force=force)
    except FileExistsError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(2)

    console.print(f"[green]Installed pre-commit hook:[/green] {hook_path}")
    console.print("[dim]Staged changes are now reviewed before every commit. "
                  "Bypass once with ONEPORT_SKIP=1 git commit ...[/dim]")


# ── cache command ──────────────────────────────────────────────────────────────

@main.group()
def cache() -> None:
    """Manage the local review cache."""


@cache.command("clear")
def cache_clear() -> None:
    """Delete all cached review results."""
    from oneport.cache import ReviewCache
    ReviewCache().clear()
    console.print("[green]Cache cleared.[/green]")


@cache.command("stats")
def cache_stats() -> None:
    """Show cache size and entry count."""
    from oneport.cache import ReviewCache
    stats = ReviewCache().stats()
    console.print(f"Entries : {stats['entries']}")
    console.print(f"Size    : {stats['size_kb']:.1f} KB")


# ── auth command ───────────────────────────────────────────────────────────────

@main.command()
def auth() -> None:
    """Show your Oneport login and token balance."""
    from oneport_account import fetch_balance, is_logged_in

    if not is_logged_in():
        console.print("[yellow]Not logged in to Oneport.[/yellow]")
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
    console.print(f"  Balance: {bal.get('balance', 0):,} tokens → gemini-flash-latest")

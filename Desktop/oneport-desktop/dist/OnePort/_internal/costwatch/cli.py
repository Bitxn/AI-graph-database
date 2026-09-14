"""
Command-line interface for Oneport Costwatch.

Entry point: `oneport-costwatch` (registered in pyproject.toml).

Commands:
  oneport-costwatch analyze [PATH]   Analyze IaC for cost waste
  oneport-costwatch price   [PATH]   Deterministic cost estimate only (no model call)
  oneport-costwatch auth             Show which model API key is configured
"""

from __future__ import annotations

import os
import sys

import click
from rich.console import Console

from costwatch.config import load_config
from costwatch.exceptions import (
    AuthError,
    ConfigError,
    CostwatchError,
    IntegrationError,
)

console = Console()
err_console = Console(stderr=True)


def _force_utf8_streams() -> None:
    """Promote stdout/stderr to UTF-8 so the inline formatter's glyphs (emoji,
    ✓, →) render instead of crashing rich on Windows' cp1252 console. Mutates
    the streams in place, so the module-level Consoles pick it up. errors=
    "replace" degrades a truly legacy console to "?" rather than raising.
    """
    import contextlib

    for stream in (sys.stdout, sys.stderr):
        # AttributeError/ValueError on a non-reconfigurable stream (e.g. already
        # detached, or not a TextIOWrapper) — safe to ignore.
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]


@click.group()
@click.version_option(package_name="oneport-costwatch")
def main() -> None:
    """Oneport Costwatch — an AI cost gate that reads your IaC. No cloud credentials needed."""
    _force_utf8_streams()


# ── analyze command ─────────────────────────────────────────────────────────────

@main.command()
@click.argument("path", default=".")
@click.option("--plan", "plan_path", default=None,
              help="Path to `terraform show -json` plan output (most accurate source).")
@click.option("--format", "-f", "fmt", type=click.Choice(["inline", "json", "github", "sarif"]),
              default=None, help="Output format ('sarif' for GitHub code scanning). Overrides config.")
@click.option("--min-severity", "-s",
              type=click.Choice(["info", "warning", "high", "critical"]),
              default=None, help="Minimum finding severity to DISPLAY. Never affects the exit code.")
@click.option("--fail-on",
              type=click.Choice(["info", "warning", "high", "critical"]),
              default=None,
              help="Fail CI (exit 1) when any non-waived finding is at/above this severity. "
                   "Off by default.")
@click.option("--budget", type=float, default=None,
              help="Fail CI (exit 1) when monthly cost (or --post added cost) exceeds this "
                   "USD cap. Off by default.")
@click.option("--config", "config_path", default=None, help="Path to config file.")
@click.option("--post", "post", is_flag=True,
              help="PATH must be a GitHub PR URL. Post a sticky cost-diff comment "
                   "when the PR increases provisioned cost (requires GITHUB_TOKEN).")
@click.option("--fail-on-increase", is_flag=True,
              help="Exit non-zero when a --post cost diff increases cost past the threshold "
                   "(use to hard-gate a PR in CI).")
def analyze(
    path: str,
    plan_path: str | None,
    fmt: str | None,
    min_severity: str | None,
    fail_on: str | None,
    budget: float | None,
    config_path: str | None,
    post: bool,
    fail_on_increase: bool,
) -> None:
    """Analyze Infrastructure-as-Code under PATH and flag cost waste.

    PATH is a file or directory of IaC (default: current directory), or a GitHub
    PR URL when using --post.
    """
    try:
        overrides: dict = {}
        if fmt:
            overrides.setdefault("output", {})["format"] = fmt
        if fail_on:
            overrides["fail_on"] = fail_on
        if budget is not None:
            overrides["budget"] = budget
        config = load_config(config_path=config_path, overrides=overrides or None)
    except ConfigError as exc:
        err_console.print(f"[bold red]Config error:[/bold red] {exc}")
        sys.exit(2)

    effective_fmt = fmt or config.output.format
    effective_sev = min_severity or config.output.min_severity

    if post:
        _run_post(path, config, config_path, effective_fmt, effective_sev, fail_on_increase)
        return

    from costwatch.analyzer import Analyzer

    try:
        analyzer = Analyzer(config=config)
        report = analyzer.analyze(path, plan_path=plan_path)
    except AuthError as exc:
        err_console.print(f"[bold red]Auth error:[/bold red] {exc}")
        sys.exit(2)
    except CostwatchError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)

    # Apply team waivers before the gate: a waived finding is still shown and
    # exported, but never trips --fail-on. Loaded from cwd (the repo root).
    from costwatch.waivers import apply_waivers, load_waivers
    waived_n = apply_waivers(report.findings, load_waivers("."))

    # The gate runs on the FULL report (before the display filter) so
    # --min-severity can hide a finding from view without hiding it from CI.
    from costwatch.result import compute_gate
    gate = compute_gate(report, fail_on=config.fail_on or None, budget=config.budget)

    _render(report.filter(effective_sev), effective_fmt)

    if waived_n and effective_fmt == "inline":
        err_console.print(
            f"[dim]{waived_n} finding(s) waived via .oneport/costwatch-waivers.yml — "
            "shown but not blocking.[/dim]"
        )
    if gate.blocking:
        err_console.print(
            "[bold red]Cost gate: BLOCKED[/bold red] — " + "; ".join(gate.reasons)
        )
        sys.exit(1)


def _run_post(
    path: str,
    config,
    config_path: str | None,
    fmt: str,
    min_severity: str,
    fail_on_increase: bool,
) -> None:
    from costwatch.analyzer import Analyzer
    from costwatch.costdiff import compute_pr_cost_diff
    from costwatch.formatters.github_fmt import build_pr_comment
    from costwatch.integrations.github import GitHubIntegration
    from costwatch.markers import COST_COMMENT_MARKER

    if "github.com" not in path or "/pull/" not in path:
        err_console.print(
            "[bold red]Error:[/bold red] --post requires PATH to be a GitHub PR URL."
        )
        sys.exit(2)

    try:
        integration = GitHubIntegration()
        pr = integration.get_pr(path)
        analyzer = Analyzer(config=config)
        diff = compute_pr_cost_diff(analyzer, integration, pr)
    except AuthError as exc:
        err_console.print(f"[bold red]Auth error:[/bold red] {exc}")
        sys.exit(2)
    except IntegrationError as exc:
        err_console.print(f"[bold red]GitHub error:[/bold red] {exc}")
        sys.exit(2)
    except CostwatchError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)

    # Waivers + the CI gate apply to the PR path too (the delta is what the
    # change is responsible for, so a --budget here caps the *added* cost).
    from costwatch.result import compute_gate
    from costwatch.waivers import apply_waivers, load_waivers
    apply_waivers(diff.report.findings, load_waivers("."))
    gate = compute_gate(diff.report, fail_on=config.fail_on or None, budget=config.budget)

    report = diff.report.filter(min_severity)
    _render(report, fmt)

    if gate.blocking:
        err_console.print(
            "[bold red]Cost gate: BLOCKED[/bold red] — " + "; ".join(gate.reasons)
        )

    if diff.delta >= config.post_threshold:
        body = build_pr_comment(diff.report.filter(min_severity))
        try:
            posted = integration.upsert_issue_comment(
                pr.owner, pr.repo, pr.number, body, COST_COMMENT_MARKER
            )
            err_console.print(
                f"[green]Posted cost comment to GitHub:[/green] {posted.get('html_url', '')}"
            )
        except IntegrationError as exc:
            err_console.print(f"[bold red]Failed to post comment:[/bold red] {exc}")
            sys.exit(2)
        if fail_on_increase:
            err_console.print(
                f"[yellow]This PR increases provisioned cost by "
                f"~${diff.delta:,.0f}/mo (threshold ${config.post_threshold:,.0f}).[/yellow]"
            )
            sys.exit(1)
    else:
        err_console.print(
            f"[dim]Cost delta ~${diff.delta:,.0f}/mo is below the "
            f"${config.post_threshold:,.0f} threshold — no comment posted.[/dim]"
        )

    # The --fail-on / --budget gate blocks the PR independently of the
    # post-threshold comment logic above.
    if gate.blocking:
        sys.exit(1)


# ── price command (deterministic only) ──────────────────────────────────────────

@main.command()
@click.argument("path", default=".")
@click.option("--plan", "plan_path", default=None, help="Path to terraform plan JSON.")
@click.option("--format", "-f", "fmt", type=click.Choice(["inline", "json"]),
              default="inline", help="Output format.")
@click.option("--config", "config_path", default=None, help="Path to config file.")
def price(path: str, plan_path: str | None, fmt: str, config_path: str | None) -> None:
    """Deterministic cost estimate only — parse IaC and price it, no model call.

    Useful in CI without any API key, or to sanity-check the parser.
    """
    from costwatch.parsers import discover_and_parse
    from costwatch.result import CostReport

    try:
        config = load_config(config_path=config_path, require_api_key=False)
    except ConfigError as exc:
        err_console.print(f"[bold red]Config error:[/bold red] {exc}")
        sys.exit(2)

    resources, notes = discover_and_parse(path, plan_path=plan_path,
                                          ignore_paths=config.ignore_paths)
    report = CostReport(path=path, resources=resources, findings=[],
                        model="(deterministic)", priced=False, notes=notes)
    _render(report, fmt)


# ── auth command ────────────────────────────────────────────────────────────────

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
    except Exception as exc:  # noqa: BLE001 - show any lookup problem plainly
        console.print(f"[yellow]Logged in, but couldn't fetch balance:[/yellow] {exc}")
        return

    console.print(f"[green]Logged in:[/green] {bal.get('email', '?')} "
                  f"[dim]({bal.get('tier', 'free')})[/dim]")
    console.print(f"  Balance: {bal.get('balance', 0):,} tokens → gemini-flash-latest")


# ── rendering ────────────────────────────────────────────────────────────────────

def _render(report, fmt: str) -> None:
    from costwatch.formatters import get_formatter

    output = get_formatter(fmt).format(report)
    if fmt == "inline":
        console.print(output)
    else:
        click.echo(output)


if __name__ == "__main__":
    main()

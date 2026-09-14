"""
Command-line interface for Oneport Testgap.

Entry point: `oneport-testgap` (registered in pyproject.toml as a console script).

Commands:
  oneport-testgap analyze [TARGET]   Find untested changed lines, rank by risk,
                                     optionally generate verified tests
  oneport-testgap auth               Check which model API key is configured

Exit codes (CI gate):
  0  no critical-risk gaps (or a graceful no-op: no changes / no pytest)
  1  changed critical-risk lines have zero coverage
  2  configuration / auth / usage error
"""

from __future__ import annotations

import os
import sys

import click
from rich.console import Console

from oneport_testgap.config import load_config
from oneport_testgap.exceptions import (
    AuthError,
    ConfigError,
    CoverageError,
    IntegrationError,
    NothingToAnalyze,
    OneportError,
)


def _force_utf8_streams() -> None:
    """Windows consoles default to cp1252, which crashes on any non-ASCII byte
    in output — including Unicode the model may return in its explanations.
    Reconfigure stdout/stderr to UTF-8 (replacing unencodable chars rather than
    raising) so the tool never dies formatting its own results."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


_force_utf8_streams()

# legacy_windows=False keeps rich off the cp1252 WriteConsole path (which
# re-encodes per character and ignores the UTF-8 reconfigure above).
console = Console(legacy_windows=False)
err_console = Console(stderr=True, legacy_windows=False)


# ── Root group ─────────────────────────────────────────────────────────────────

@click.group()
@click.version_option(package_name="oneport-testgap")
def main() -> None:
    """Oneport Testgap — finds untested changed code and generates tests it has
    actually executed and verified."""


# ── analyze command ────────────────────────────────────────────────────────────

@main.command()
@click.argument("target", default="--staged")
@click.option("--staged", "use_staged", is_flag=True, help="Analyze staged changes (git diff --cached). Default.")
@click.option("--head", "use_head", is_flag=True, help="Analyze the last commit (git diff HEAD~1).")
@click.option(
    "--generate", "-g", "generate_n", type=int, default=0, metavar="N",
    help="Generate pytest tests for the top N riskiest gaps. Each test is "
         "EXECUTED before being shown; failing tests are discarded. Verified "
         "tests land in tests/generated/ (never auto-committed).",
)
@click.option(
    "--post", "post_report", is_flag=True,
    help="Post the gap report as an inline GitHub PR review (requires "
         "GITHUB_TOKEN). Only valid when TARGET is a GitHub PR URL.",
)
@click.option(
    "--min-risk", "-r", "min_risk",
    type=click.Choice(["high", "medium", "low"]),
    default=None,
    help="Minimum risk level to DISPLAY. Overrides .oneportrc. Never affects the "
         "exit code — use --fail-on for that.",
)
@click.option(
    "--fail-on", "fail_on",
    type=click.Choice(["critical", "high", "medium", "low"]),
    default=None,
    help="Risk level that FAILS CI (exit 1). Default: critical. Waived gaps "
         "never block.",
)
@click.option(
    "--format", "-f", "fmt",
    type=click.Choice(["inline", "json", "sarif"]),
    default=None,
    help="Output format ('sarif' for GitHub code scanning). Overrides .oneportrc.",
)
@click.option(
    "--coverage-file", "coverage_file", default=None,
    help="Path to an existing coverage.xml (Cobertura). Skips running pytest.",
)
@click.option("--config", "config_path", default=None, help="Path to .oneportrc file.")
def analyze(
    target: str,
    use_staged: bool,
    use_head: bool,
    generate_n: int,
    post_report: bool,
    min_risk: str | None,
    fail_on: str | None,
    fmt: str | None,
    coverage_file: str | None,
    config_path: str | None,
) -> None:
    """Find changed lines with zero test coverage, ranked by risk.

    TARGET can be:
      - omitted / --staged:           staged changes (git diff --cached)
      - --head:                       the last commit
      - a GitHub PR URL:              https://github.com/org/repo/pull/42
        (coverage still runs locally — check out the PR branch first)
    """
    from oneport_testgap.analyzer import Analyzer
    from oneport_testgap.coverage_utils import missing_tooling

    if use_staged:
        target = "--staged"
    elif use_head:
        target = "--head"

    try:
        overrides: dict = {}
        if fmt or min_risk:
            overrides["output"] = {}
            if fmt:
                overrides["output"]["format"] = fmt
            if min_risk:
                overrides["output"]["min_risk"] = min_risk
        if fail_on:
            overrides["fail_on"] = fail_on
        config = load_config(config_path=config_path, overrides=overrides or None)
    except (ConfigError, AuthError) as exc:
        err_console.print(f"[bold red]Config error:[/bold red] {exc}")
        sys.exit(2)

    effective_format = fmt or config.output.format

    def _skip(message: str) -> None:
        """A skip is a clean no-op, not a failure — emit valid JSON in json mode
        so orchestrators (op ship) read it as a pass, not an unparseable error."""
        if effective_format == "json":
            import json
            click.echo(json.dumps(
                {"skipped": True, "reason": message,
                 "findings": [], "summary": {"findings": 0}}))
        else:
            console.print(f"[yellow]Skipping test-gap analysis:[/yellow] {message}")

    # Graceful no-op when the coverage tooling isn't there (unless the user
    # brought their own coverage.xml).
    if not coverage_file and (message := missing_tooling()):
        _skip(message)
        return

    try:
        report, resolved = Analyzer(config=config).analyze(
            target, coverage_file=coverage_file, generate=generate_n
        )
    except NothingToAnalyze as exc:
        _skip(str(exc))
        return
    except CoverageError as exc:
        # Coverage couldn't be measured — almost always the TARGET repo's own
        # test suite won't run in this environment (missing dev deps, a pytest
        # plugin conflict, an INTERNALERROR). That's an environment limitation,
        # not a testgap failure, so it degrades to a skip: valid JSON that op
        # ship reads as SKIPPED, never a bare "Error:" + pytest dump on stdout
        # that the orchestrator can't parse. First line only — the pytest tail is
        # noise in a one-line gate summary.
        _skip(f"could not measure coverage here — {str(exc).splitlines()[0]}")
        return
    except AuthError as exc:
        err_console.print(f"[bold red]Auth error:[/bold red] {exc}")
        err_console.print("Log in to Oneport: `oneport-account login <token>` "
                          "(free token at https://oneport.dev).")
        sys.exit(2)
    except OneportError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)

    # Apply team waivers before the gate: a waived gap is still shown and
    # exported, but never trips --fail-on. Loaded from cwd (the repo root).
    from oneport_testgap.waivers import apply_waivers, load_waivers
    apply_waivers(report.gaps, load_waivers("."))

    # Render output
    effective_format = fmt or config.output.format
    effective_min_risk = min_risk or config.output.min_risk
    _render(report, effective_format, effective_min_risk)

    # Post inline PR review, if requested
    if post_report:
        if not report.pr_ref:
            err_console.print(
                "[bold red]Error:[/bold red] --post only works when TARGET is a GitHub PR URL."
            )
            sys.exit(2)
        try:
            from oneport_testgap.formatters.github_fmt import build_pr_review
            from oneport_testgap.integrations.github import GitHubIntegration

            payload = build_pr_review(report, resolved.diff, min_risk=effective_min_risk)
            posted = GitHubIntegration().post_review(
                report.pr_ref["owner"],
                report.pr_ref["repo"],
                report.pr_ref["number"],
                body=payload["body"],
                comments=payload["comments"],
                event=payload["event"],
            )
            # stderr, not stdout — keeps `--format json` output machine-parseable
            err_console.print(
                f"[green]Posted gap report to GitHub:[/green] {posted.get('html_url', '')}"
            )
        except IntegrationError as exc:
            err_console.print(f"[bold red]Failed to post report:[/bold red] {exc}")
            sys.exit(2)

    # Exit 1 if changed lines at/above the fail-on threshold have zero coverage
    # (CI gate). Threshold defaults to critical; waived gaps never block.
    if report.is_blocking(config.fail_on):
        sys.exit(1)


def _render(report, fmt: str, min_risk: str) -> None:
    from oneport_testgap.formatters.inline import format_inline
    from oneport_testgap.formatters.json_fmt import format_json
    from oneport_testgap.formatters.sarif import format_sarif

    if fmt == "json":
        click.echo(format_json(report, min_risk=min_risk))
    elif fmt == "sarif":
        click.echo(format_sarif(report, min_risk=min_risk))
    else:
        click.echo(format_inline(report, min_risk=min_risk))


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
    console.print(f"  Balance: {bal.get('balance', 0):,} tokens -> gemini-flash-latest")

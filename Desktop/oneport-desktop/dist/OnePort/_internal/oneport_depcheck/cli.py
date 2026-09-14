"""
Command-line interface for oneport-depcheck.

Entry points (pyproject console scripts): `oneport-depcheck` and `depcheck`.

  oneport-depcheck scan [PATH] [--post PR_URL] [--min-severity S]
                        [--fail-on reachable|any|none] [--format inline|json]
                        [--fix] [--no-llm] [--no-license] [--no-cache]
  oneport-depcheck learn "dev-dependency CVEs are warn-only"
  oneport-depcheck cache clear|stats
  oneport-depcheck auth

Exit codes: 0 = gate passed, 1 = gate failed (per --fail-on), 2 = usage/auth error.
"""

from __future__ import annotations

import sys

import click

from oneport_depcheck import __version__
from oneport_depcheck.config import load_config
from oneport_depcheck.exceptions import (
    AuthError,
    ConfigError,
    DepcheckError,
    IntegrationError,
    OSVError,
)
from oneport_depcheck.result import Reachability, ScanResult, Severity

_SEVERITY_CHOICES = ["low", "medium", "high", "critical"]
_SEV_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


@click.group()
@click.version_option(version=__version__, prog_name="oneport-depcheck")
def main() -> None:
    """Dependency CVE gate — deterministic detection (OSV.dev), LLM triage."""
    # Advisory text from OSV may contain characters a cp1252 Windows console
    # can't encode; degrade those to '?' instead of crashing the scan.
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass


@main.command()
@click.argument("path", default=".", required=False,
                type=click.Path(exists=True, file_okay=False))
@click.option("--post", "pr_url", default=None, metavar="PR_URL",
              help="Post findings to a GitHub PR: inline comments on the exact "
                   "manifest lines + a sticky summary table (requires GITHUB_TOKEN).")
@click.option("--min-severity", "-s", type=click.Choice(_SEVERITY_CHOICES),
              default="low", show_default=True,
              help="Hide findings below this severity.")
@click.option("--fail-on", type=click.Choice(["reachable", "any", "none"]),
              default="reachable", show_default=True,
              help="CI gate: exit 1 when matching findings exist. "
                   "'reachable' fails only on LLM-confirmed reachable vulns "
                   "(UNTRIAGED counts as reachable — fail safe).")
@click.option("--format", "-f", "fmt", type=click.Choice(["inline", "json", "sarif"]),
              default="inline", show_default=True,
              help="Output format ('sarif' for GitHub code scanning).")
@click.option("--fix", "show_fix", is_flag=True,
              help="Print a consolidated fix plan: the exact manifest line "
                   "change (or lockfile regeneration command) per package.")
@click.option("--no-llm", is_flag=True,
              help="Skip the LLM triage layer. Detection still runs; findings "
                   "are UNTRIAGED and --fail-on reachable treats them as reachable.")
@click.option("--no-license", is_flag=True, help="Skip license checks.")
@click.option("--no-cache", is_flag=True, help="Bypass the 24h OSV/registry cache.")
def scan(
    path: str,
    pr_url: str | None,
    min_severity: str,
    fail_on: str,
    fmt: str,
    show_fix: bool,
    no_llm: bool,
    no_license: bool,
    no_cache: bool,
) -> None:
    """Scan PATH's dependency manifests for known CVEs and triage exploitability.

    Detection is deterministic: manifests are parsed locally and each pinned
    package+version is checked against OSV.dev. The model only ever judges
    whether a CONFIRMED vulnerability is reachable from your code.
    """
    from oneport_depcheck.scanner import run_scan

    try:
        config = load_config(require_llm=not no_llm)
    except ConfigError as exc:
        _err(f"Config error: {exc}")
        sys.exit(2)

    try:
        result = run_scan(
            path,
            config,
            use_llm=not no_llm,
            use_cache=not no_cache,
            check_license=not no_license,
        )
    except OSVError as exc:
        _err(f"OSV.dev error: {exc}")
        _err("Refusing to report a possibly-incomplete scan as clean.")
        sys.exit(2)
    except AuthError as exc:
        _err(f"Auth error: {exc}")
        sys.exit(2)
    except DepcheckError as exc:
        _err(f"Error: {exc}")
        sys.exit(1)

    # Apply team waivers before the gate: a waived finding is still shown and
    # exported, but never trips --fail-on. Loaded from cwd (the repo root).
    from oneport_depcheck.waivers import apply_waivers, load_waivers
    apply_waivers(result.findings, load_waivers("."))

    result.findings = [
        f for f in result.findings
        if f.severity.rank >= _SEV_RANK[min_severity] or f.severity == Severity.UNKNOWN
    ]

    _render(result, fmt)

    if show_fix and fmt == "inline":
        _render_fix_plan(result)

    if pr_url:
        _post_to_github(result, pr_url)

    if _gate_fails(result, fail_on):
        _err(f"Gate failed (--fail-on {fail_on}).")
        sys.exit(1)


def _gate_fails(result: ScanResult, fail_on: str) -> bool:
    # Waived findings are approved on the record and never block, at any level.
    findings = [f for f in result.findings if not f.waived]
    if fail_on == "none":
        return False
    if fail_on == "any":
        return bool(findings)
    # reachable: REACHABLE fails; UNTRIAGED fails too (unverified ≠ safe).
    return any(
        f.reachability in (Reachability.REACHABLE, Reachability.UNTRIAGED)
        for f in findings
    )


def _render(result: ScanResult, fmt: str) -> None:
    if fmt == "json":
        from oneport_depcheck.formatters.json_fmt import format_json
        click.echo(format_json(result))
    elif fmt == "sarif":
        from oneport_depcheck.formatters.sarif import format_sarif
        click.echo(format_sarif(result))
    else:
        from oneport_depcheck.formatters.inline import format_inline
        click.echo(format_inline(result))


def _render_fix_plan(result: ScanResult) -> None:
    """One exact change per vulnerable package, deduplicated, committable."""
    seen: set[str] = set()
    lines: list[str] = []
    for f in result.findings:
        if not f.fix_suggestion or f.package in seen:
            continue
        seen.add(f.package)
        lines.append(f"  {f.manifest}:{f.manifest_line}")
        lines.append(f"    {f.fix_suggestion}")
    if lines:
        click.echo("Fix plan:")
        for line in lines:
            click.echo(line)
    else:
        click.echo("Fix plan: nothing to fix.")


def _post_to_github(result: ScanResult, pr_url: str) -> None:
    from oneport_depcheck.formatters.github_fmt import build_pr_review, build_sticky_summary
    from oneport_depcheck.integrations.github import GitHubIntegration

    try:
        gh = GitHubIntegration()
        pr = gh.get_pr(pr_url)

        review = build_pr_review(result, pr.diff)
        if review["comments"]:
            posted = gh.post_review(
                pr.owner, pr.repo, pr.number,
                body=review["body"], comments=review["comments"],
            )
            _err(f"Posted inline review: {posted.get('html_url', '')}")

        sticky = build_sticky_summary(result, head_sha=pr.head_sha)
        from oneport_depcheck.markers import STICKY_MARKER
        posted = gh.upsert_issue_comment(
            pr.owner, pr.repo, pr.number, body=sticky, marker=STICKY_MARKER
        )
        _err(f"Updated summary comment: {posted.get('html_url', '')}")
    except IntegrationError as exc:
        _err(f"Failed to post to GitHub: {exc}")
        sys.exit(2)


@main.command()
@click.argument("guideline")
def learn(guideline: str) -> None:
    """Teach depcheck a team rule, e.g. "dev-dependency CVEs are warn-only".

    Appended to .oneport/guidelines.md (shared with all Oneport tools) and
    injected into every triage prompt.
    """
    from oneport_depcheck.guidelines import append_guideline

    try:
        written = append_guideline(guideline)
    except ValueError as exc:
        _err(f"Error: {exc}")
        sys.exit(2)
    click.echo(f"Learned. Added to {written}:")
    click.echo(f"  {guideline}")


@main.group()
def cache() -> None:
    """Manage the local OSV/registry cache (~/.oneport-depcheck/cache)."""


@cache.command("clear")
def cache_clear() -> None:
    """Delete all cached OSV and registry responses."""
    from oneport_depcheck.cache import ScanCache
    n = ScanCache().clear()
    click.echo(f"Cleared {n} cache entr{'y' if n == 1 else 'ies'}.")


@cache.command("stats")
def cache_stats() -> None:
    """Show cache entry count and size."""
    from oneport_depcheck.cache import ScanCache
    stats = ScanCache().stats()
    click.echo(f"Entries : {stats['entries']}")
    click.echo(f"Size    : {stats['size_kb']} KB")


@main.command()
def auth() -> None:
    """Show your Oneport login and token balance."""
    from oneport_account import fetch_balance, is_logged_in

    if not is_logged_in():
        click.echo("Not logged in to Oneport.")
        click.echo("  Run: oneport-account login <token>   (free token at https://oneport.dev)")
        click.echo("  Detection works without login: oneport-depcheck scan --no-llm")
        return

    try:
        bal = fetch_balance()
    except Exception as exc:  # noqa: BLE001 - surface any lookup issue plainly
        click.echo(f"Logged in, but couldn't fetch balance: {exc}")
        return

    click.echo(f"Logged in: {bal.get('email', '?')} ({bal.get('tier', 'free')})")
    click.echo(f"  Balance: {bal.get('balance', 0):,} tokens -> gemini-flash-latest")


def _err(message: str) -> None:
    click.echo(message, err=True)

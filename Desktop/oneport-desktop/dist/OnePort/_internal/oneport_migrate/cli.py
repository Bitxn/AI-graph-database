"""
Command-line interface for Oneport Migrate.

Entry point: `oneport-migrate` (registered in pyproject.toml).

Commands:
  oneport-migrate check <target>   Check migration files / --staged / --head / a PR URL
  oneport-migrate rules list       List all rule IDs

Exit codes (CI contract, same as oneport-review):
  0  clean (or warnings/info only)
  1  blocking findings (error/critical) — computed from the FULL finding set,
     never after display filtering
  2  usage/config/auth errors
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

import sys

import click
from rich.console import Console
from rich.markup import escape

from oneport_migrate.config import load_config
from oneport_migrate.exceptions import (
    AuthError,
    ConfigError,
    IntegrationError,
    OneportMigrateError,
    ParseError,
)

console = Console()
err_console = Console(stderr=True)


def _is_repo_root(target: str) -> bool:
    """True only when TARGET is the git repo root itself (e.g. `check .`).

    An explicit file or subdirectory (`check app/migrations/0042.py`,
    `check migrations/`) is a deliberate "scan exactly this" and is left alone.
    """
    import subprocess
    from pathlib import Path

    try:
        path = Path(target).resolve()
        if not path.is_dir():
            return False
        top = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        if top.returncode != 0:
            return False
        return Path(top.stdout.strip()).resolve() == path
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


@click.group()
@click.version_option(package_name="oneport-migrate")
def main() -> None:
    """Oneport Migrate — the database-migration safety gate for Python teams."""


@main.command()
@click.argument("target", required=False)
@click.option("--staged", "use_staged", is_flag=True,
              help="Check migration files staged for commit.")
@click.option("--all", "scan_all", is_flag=True,
              help="Audit EVERY migration in the target, including ones already applied "
                   "in production. Noisy by design — for an audit, not a pre-ship gate.")
@click.option("--head", "use_head", is_flag=True,
              help="Check migration files changed in the last commit.")
@click.option("--post", "post_review", is_flag=True,
              help="Post findings as an inline GitHub PR review (requires GITHUB_TOKEN). "
                   "Only valid when TARGET is a GitHub PR URL.")
@click.option("--format", "-f", "fmt", type=click.Choice(["inline", "json", "sarif"]), default=None,
              help="Output format ('sarif' for GitHub code scanning). Overrides .oneportmigraterc.")
@click.option("--db", type=click.Choice(["postgres", "mysql", "sqlite"]), default=None,
              help="Database dialect the rules run against (default: postgres).")
@click.option("--min-severity", "-s",
              type=click.Choice(["info", "warning", "error", "critical"]), default=None,
              help="Minimum severity to DISPLAY. Never affects the exit code.")
@click.option("--fail-on",
              type=click.Choice(["info", "warning", "error", "critical"]), default=None,
              help="Severity that BLOCKS (exit 1). Default: error (error + critical).")
@click.option("--no-llm", is_flag=True,
              help="Skip the LLM blast-radius layer; deterministic rules only.")
@click.option("--config", "config_path", default=None, help="Path to .oneportmigraterc.")
def check(
    target: str | None,
    use_staged: bool,
    scan_all: bool,
    use_head: bool,
    post_review: bool,
    fmt: str | None,
    db: str | None,
    min_severity: str | None,
    fail_on: str | None,
    no_llm: bool,
    config_path: str | None,
) -> None:
    """Check migrations for unsafe operations before they ship.

    TARGET can be:
      - a migration file:      app/migrations/0042_drop_legacy.py
      - a directory to scan:   migrations/
      - a GitHub PR URL:       https://github.com/org/repo/pull/42

    Or use --staged / --head instead of TARGET.
    """
    if use_staged:
        target = "--staged"
    elif use_head:
        target = "--head"
    elif target and not scan_all and _is_repo_root(target):
        # `check .` must mean "gate what I'm shipping", not "audit six years of
        # history". Pointing it at saleor's repo root scanned every migration
        # since 2018 — all long since applied in production — and emitted 2,472
        # findings, 2,112 of them "ERROR". Nobody reads that; it reads as noise
        # and destroys trust in a gate that is otherwise exact. Same principle as
        # secrets: scope to the change by default, auditing history is opt-in.
        # Neutral wording: this prints BEFORE we know whether any migrations
        # exist, so it must not assert that they do — FastAPI has none, and
        # "your history contains migrations already applied" read as false there.
        err_console.print(
            "[dim]Gating the last commit (--head) — pass --all to audit the full "
            "migration history instead.[/dim]"
        )
        target = "--head"
    if not target:
        err_console.print(
            "[bold red]Error:[/bold red] give a TARGET (file, directory, or PR URL) "
            "or one of --staged / --head."
        )
        sys.exit(2)

    from oneport_migrate.checker import Checker

    try:
        overrides: dict = {}
        if fmt:
            overrides.setdefault("output", {})["format"] = fmt
        if db:
            overrides["db"] = db
        if fail_on:
            overrides["fail_on"] = fail_on

        config = load_config(config_path=config_path, overrides=overrides or None)
        checker = Checker(config=config)
        result = checker.check(target, use_llm=not no_llm)

    # escape() the exception text: a message containing '[...]' (git output,
    # a migration path, SQL) would otherwise be parsed as Rich markup and crash
    # the CLI with a MarkupError instead of showing the error.
    except AuthError as exc:
        err_console.print(f"[bold red]Auth error:[/bold red] {escape(str(exc))}")
        sys.exit(2)
    except ConfigError as exc:
        err_console.print(f"[bold red]Config error:[/bold red] {escape(str(exc))}")
        sys.exit(2)
    except FileNotFoundError as exc:
        err_console.print(f"[bold red]Not found:[/bold red] {escape(str(exc))}")
        sys.exit(2)
    except ParseError as exc:
        err_console.print(f"[bold red]Parse error:[/bold red] {escape(str(exc))}")
        sys.exit(2)
    except OneportMigrateError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {escape(str(exc))}")
        sys.exit(1)

    # Render (display filter only — the exit decision uses the full set).
    effective_format = fmt or config.output.format
    effective_severity = min_severity or config.output.min_severity
    _render(result.filter(effective_severity), effective_format)

    if post_review:
        if not result.pr_ref:
            err_console.print(
                "[bold red]Error:[/bold red] --post only works when TARGET is a GitHub PR URL."
            )
            sys.exit(2)
        try:
            posted = checker.post_github_review(result)
            if posted is None:
                err_console.print(
                    "[dim]This revision was already reviewed — nothing new to post.[/dim]"
                )
            else:
                err_console.print(
                    f"[green]Posted review to GitHub:[/green] {posted.get('html_url', '')}"
                )
        except IntegrationError as exc:
            err_console.print(f"[bold red]Failed to post review:[/bold red] {exc}")
            sys.exit(2)

    if result.has_blocking_issues:
        sys.exit(1)


def _render(result, fmt: str) -> None:
    from oneport_migrate.formatters import InlineFormatter, JsonFormatter, SarifFormatter

    if fmt == "json":
        formatter = JsonFormatter()
    elif fmt == "sarif":
        formatter = SarifFormatter()
    else:
        formatter = InlineFormatter()
    # Inline output carries its own ANSI styling — echo it raw so rich doesn't
    # re-interpret [CRITICAL] as markup.
    click.echo(formatter.format(result))


@main.group()
def rules() -> None:
    """Manage migration-safety rules."""


@rules.command("list")
@click.option("--category", "-c", default=None, help="Filter by category.")
def rules_list(category: str | None) -> None:
    """List all rule IDs, severities, and descriptions."""
    from oneport_migrate.rules.loader import load_rule_set

    rule_set = load_rule_set()
    for rule in rule_set.all_rules:
        if category and rule.category != category:
            continue
        severity = getattr(rule.severity, "value", rule.severity)
        console.print(f"[bold]{rule.id}[/bold]  [{severity}] ({rule.category}) {rule.description}")

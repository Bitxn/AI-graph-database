"""
Command-line interface for Oneport ApiDiff.

Entry point: `oneport-apidiff` (registered in pyproject.toml as a console script).

  oneport-apidiff check --staged                 gate what you're about to commit
  oneport-apidiff check --head                   gate the last commit
  oneport-apidiff check --base main              gate the branch vs main
  oneport-apidiff check <github-pr-url> --post   gate a PR and post the verdict

Exit codes (CI contract, same as oneport-review):
  0  no breaking changes (or --allow-breaking)
  1  BREAKING changes found
  2  usage / config / auth error
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

from oneport_apidiff import __version__
from oneport_apidiff.config import load_config
from oneport_apidiff.exceptions import (
    ApidiffError,
    AuthError,
    ConfigError,
    DiffError,
    IntegrationError,
)

console = Console()
err_console = Console(stderr=True)


@click.group()
@click.version_option(version=__version__, package_name="oneport-apidiff")
def main() -> None:
    """Oneport ApiDiff — breaking-change gate for PRs, straight from the code diff."""


@main.command()
@click.argument("pr_url", required=False, default=None)
@click.option("--staged", "use_staged", is_flag=True, help="Check staged changes (git add'd).")
@click.option("--head", "use_head", is_flag=True, help="Check the last commit (HEAD~1..HEAD).")
@click.option(
    "--base",
    "base_branch",
    default=None,
    metavar="BRANCH",
    help="Check the working tree against the merge-base with BRANCH.",
)
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["inline", "json", "sarif"]),
    default="inline",
    help="Output format. 'sarif' emits SARIF 2.1.0 for GitHub code scanning / dashboards.",
)
@click.option(
    "--post",
    "post_verdict",
    is_flag=True,
    help="Post the verdict to the PR: sticky verdict table + inline comments on "
    "the changed signatures (requires GITHUB_TOKEN; PR URL targets only).",
)
@click.option(
    "--allow-breaking",
    is_flag=True,
    help="Exit 0 even when BREAKING changes are found (report-only mode).",
)
@click.option(
    "--no-llm",
    is_flag=True,
    help="Skip model classification — deterministic verdicts only, no API key needed.",
)
@click.option("--config", "config_path", default=None, help="Path to .oneportrc file.")
def check(
    pr_url: str | None,
    use_staged: bool,
    use_head: bool,
    base_branch: str | None,
    fmt: str,
    post_verdict: bool,
    allow_breaking: bool,
    no_llm: bool,
    config_path: str | None,
) -> None:
    """Detect API breaking changes in a change set.

    The change set is one of: --staged, --head, --base BRANCH, or a GitHub
    PR URL as the argument. With no source given, --staged is assumed.
    """
    from oneport_apidiff.engine import check_local, check_pr

    sources = [bool(pr_url), use_staged, use_head, base_branch is not None]
    if sum(sources) > 1:
        err_console.print(
            "[bold red]Error:[/bold red] pick ONE of: PR URL, --staged, --head, --base BRANCH."
        )
        sys.exit(2)
    if not any(sources):
        use_staged = True  # the pre-commit default

    if post_verdict and not pr_url:
        err_console.print(
            "[bold red]Error:[/bold red] --post only works when the target is a GitHub PR URL."
        )
        sys.exit(2)

    try:
        config = load_config(config_path=config_path, require_api_key=not no_llm)
    except ConfigError as exc:
        err_console.print(f"[bold red]Config error:[/bold red] {exc}")
        sys.exit(2)

    try:
        if pr_url:
            result = check_pr(pr_url, config=config, use_llm=not no_llm)
        elif use_head:
            result = check_local("head", config=config, use_llm=not no_llm)
        elif base_branch is not None:
            result = check_local(
                "base", config=config, base_branch=base_branch, use_llm=not no_llm
            )
        else:
            result = check_local("staged", config=config, use_llm=not no_llm)
    except AuthError as exc:
        err_console.print(f"[bold red]Auth error:[/bold red] {exc}")
        err_console.print("Set ANTHROPIC_API_KEY (Claude) or GEMINI_API_KEY (Gemini).")
        sys.exit(2)
    except (DiffError, IntegrationError) as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(2)
    except ApidiffError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)

    _render(result, fmt)

    if post_verdict:
        _post(result)

    if result.has_breaking and not allow_breaking:
        sys.exit(1)


def _render(result, fmt: str) -> None:
    from oneport_apidiff.formatters.inline import format_inline
    from oneport_apidiff.formatters.json_fmt import format_json
    from oneport_apidiff.formatters.sarif_fmt import format_sarif

    if fmt == "json":
        output = format_json(result)
    elif fmt == "sarif":
        output = format_sarif(result)
    else:
        output = format_inline(result)
    click.echo(output)


def _post(result) -> None:
    """Upsert the sticky verdict table; post the inline review once per head SHA."""
    from oneport_apidiff.formatters.github_fmt import build_pr_review, build_sticky_comment
    from oneport_apidiff.integrations.github import GitHubIntegration
    from oneport_apidiff.markers import STICKY_MARKER, extract_checked_sha

    pr_ref = result.pr_ref or {}
    owner, repo, number = pr_ref.get("owner"), pr_ref.get("repo"), pr_ref.get("number")
    head_sha = pr_ref.get("head_sha", "")
    if not (owner and repo and number):
        err_console.print("[bold red]Error:[/bold red] no PR reference to post to.")
        sys.exit(2)

    gh = GitHubIntegration()
    try:
        # Re-run against an already-checked head SHA? Update nothing, add nothing.
        already_checked = False
        for comment in gh.list_issue_comments(owner, repo, number):
            body = comment.get("body") or ""
            if STICKY_MARKER in body and extract_checked_sha(body) == head_sha:
                already_checked = True
                break
        if already_checked:
            err_console.print(
                f"[dim]Head {head_sha[:7]} already checked — verdict comment is current, "
                "nothing re-posted.[/dim]"
            )
            return

        posted = gh.upsert_issue_comment(
            owner, repo, number, body=build_sticky_comment(result), marker=STICKY_MARKER
        )
        err_console.print(
            f"[green]Posted verdict table:[/green] {posted.get('html_url', '')}"
        )

        if result.changes:
            review = build_pr_review(result, pr_ref.get("diff", ""))
            posted_review = gh.post_review(
                owner,
                repo,
                number,
                body=review["body"],
                comments=review["comments"],
                event=review["event"],
            )
            err_console.print(
                f"[green]Posted inline review:[/green] {posted_review.get('html_url', '')}"
            )
    except IntegrationError as exc:
        err_console.print(f"[bold red]Failed to post to GitHub:[/bold red] {exc}")
        sys.exit(2)

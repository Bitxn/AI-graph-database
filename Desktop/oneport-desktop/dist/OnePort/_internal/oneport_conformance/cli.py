"""
oneport-conformance — does this change match the team's stated intent?

  oneport-conformance check                 check the last commit vs .oneport/intent.md
  oneport-conformance check --staged        check staged changes
  oneport-conformance check --base main      check the whole branch vs main
  oneport-conformance check -f json          machine-readable
  oneport-conformance check --intent spec.md --fail-on high

Exit codes (the suite contract): 0 = conforms / nothing to check, 1 = blocking
deviation(s), 2 = usage or service error.
"""

from __future__ import annotations

import sys

import click

from oneport_conformance import __version__, engine, formatters
from oneport_conformance.exceptions import OneportError
from oneport_conformance.intent import find_intent


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="oneport-conformance")
def main() -> None:
    """Verify AI-written changes against the product team's intent."""


@main.command()
@click.argument("repo", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--intent", "intent_path", type=click.Path(),
              help="Intent doc (default: auto-find .oneport/intent.md).")
@click.option("--staged", is_flag=True, help="Check staged changes (git add) instead of the last commit.")
@click.option("--base", default=None, help="Check the current branch against this base branch.")
@click.option("-f", "--format", "fmt", type=click.Choice(["human", "json", "sarif"]),
              default="human", help="Output format.")
@click.option("--fail-on", type=click.Choice(["low", "medium", "high"]), default="medium",
              help="Minimum severity that blocks (exit 1). Default: medium.")
@click.option("--model", default=engine.DEFAULT_MODEL, help="Model to judge with.")
@click.option("--gemini-key", default=None,
              help="Use your own Gemini key (or set GEMINI_API_KEY) — spends no OnePort tokens.")
@click.option("--waivers", "waiver_path", type=click.Path(),
              help="Waivers file (default: auto-find .oneport/conformance-waivers.json).")
def check(repo, intent_path, staged, base, fmt, fail_on, model, gemini_key, waiver_path) -> None:
    """Check a change in REPO (default: current dir) against the intent doc."""
    # Resolve the intent doc up front so a missing one fails clearly, not mid-run.
    if not intent_path:
        found = find_intent(repo)
        if not found:
            _die("No intent doc found. Create .oneport/intent.md describing how "
                 "the software should behave, or pass --intent <file>.")
        intent_path = str(found)

    mode = "staged" if staged else ("branch" if base else "head")

    try:
        result = engine.check(
            intent_path=intent_path, repo=repo, mode=mode,
            base=base or "main", model=model, waiver_path=waiver_path,
            gemini_key=gemini_key,
        )
    except OneportError as exc:
        _die(str(exc))
    except Exception as exc:  # never leak a raw traceback as a "verdict"
        _die(f"Unexpected error (reporting as inconclusive, not a pass): {exc}")

    if fmt == "json":
        click.echo(formatters.to_json(result, fail_on))
    elif fmt == "sarif":
        click.echo(formatters.to_sarif(result, fail_on))
    else:
        click.echo(formatters.to_human(result, fail_on))

    # Skip (empty diff) is exit 0 — there was nothing to block, and we said so.
    if not result.skipped and result.blocking(fail_on):
        sys.exit(1)


def _die(msg: str) -> None:
    click.echo(f"error: {msg}", err=True)
    sys.exit(2)


if __name__ == "__main__":
    main()

"""
oneport-mcp — OnePort inside your AI coding agent.

  oneport-mcp install claude      wire up Claude Code: MCP server + write-guard hook
  oneport-mcp serve               run the MCP server (stdio; agents launch this)
  oneport-mcp guard               the PreToolUse hook entry (reads stdin, exit 2 blocks)
  oneport-mcp status              which gates are installed / login state
"""
from __future__ import annotations

import sys

import click


@click.group()
@click.version_option(package_name="oneport-mcp")
def main() -> None:
    """OnePort inside your AI coding agent — MCP server + write-guard hooks."""


@main.command()
def serve() -> None:
    """Run the OnePort MCP server on stdio (Claude Code/Cursor/Codex launch this)."""
    from oneport_mcp.server import serve as _serve
    _serve()


@main.command()
def guard() -> None:
    """Hook entry: scan the pending Write/Edit for secrets; exit 2 blocks it."""
    from oneport_mcp.guard import run_guard
    sys.exit(run_guard(sys.stdin.read()))


@main.command()
@click.argument("agent", type=click.Choice(["claude"]))
@click.option("--dir", "project_dir", default=".", type=click.Path(file_okay=False),
              help="Project directory to install into (default: current).")
@click.option("--global-hooks", is_flag=True,
              help="Install the write-guard for ALL projects (~/.claude) instead of this one.")
def install(agent: str, project_dir: str, global_hooks: bool) -> None:
    """Wire OnePort into an AI coding agent (currently: claude = Claude Code)."""
    from oneport_mcp.installer import install_claude
    try:
        notes = install_claude(project_dir, global_hooks=global_hooks)
    except RuntimeError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    for line in notes:
        click.echo(f"  {line}")
    click.echo("\nOnePort is wired in: the agent can run ship_gate/scan_secrets etc., "
               "and the write-guard blocks secrets before they land.")


@main.command()
def status() -> None:
    """Show which OnePort gates are installed and whether you're logged in."""
    from oneport_mcp.gates import GATES, installed, logged_in
    click.echo(f"logged in to OnePort proxy: {'yes' if logged_in() else 'no'}")
    for g in GATES:
        mark = "installed" if installed(g) else f"missing   (pip install {g.pip})"
        metered = "  [metered]" if g.metered else ""
        click.echo(f"  {g.id:<14} {mark}{metered}")


if __name__ == "__main__":
    main()

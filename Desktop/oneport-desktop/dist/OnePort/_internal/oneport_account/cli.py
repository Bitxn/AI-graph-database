"""
`oneport-account` — manage your Oneport account from the terminal.

  oneport-account login <op_live_...>   save your token (get one at oneport.dev)
  oneport-account balance               show tokens remaining + usage
  oneport-account redeem <CODE>         redeem a free-token code
  oneport-account logout                remove the token from this machine
  oneport-account whoami                show the logged-in account

Also available through the umbrella as `op account ...`.
"""

from __future__ import annotations

import sys

import click
from rich.console import Console

from oneport_account import __version__
from oneport_account.client import fetch_balance, redeem_code, validate_token
from oneport_account.config import DEFAULT_BUY_URL
from oneport_account.credentials import Credentials, clear, load, save
from oneport_account.exceptions import AccountError

console = Console()
err = Console(stderr=True)


@click.group()
@click.version_option(version=__version__, prog_name="oneport-account")
def main() -> None:
    """Oneport account — login, token balance, redeem codes."""


@main.command()
@click.argument("token")
@click.option("--api-url", default="", help="Override the backend URL (self-host/staging).")
def login(token, api_url):
    """Save your op_live_... token after validating it."""
    try:
        info = validate_token(token, api_url=api_url)
    except AccountError as exc:
        err.print(f"[bold red]Login failed:[/bold red] {exc}")
        sys.exit(2)
    save(Credentials(token=token, email=info.get("email", ""), api_url=api_url))
    console.print(f"[green]Logged in[/green] as {info.get('email', '(unknown)')} · "
                 f"[cyan]{info.get('balance', 0):,}[/cyan] tokens · tier {info.get('tier', 'free')}")


@main.command()
def balance():
    """Show tokens remaining and recent usage by tool."""
    try:
        info = fetch_balance()
    except AccountError as exc:
        err.print(f"[bold red]{exc}[/bold red]")
        sys.exit(2)
    console.print(f"\n[bold]{info.get('email', '')}[/bold]  [dim]· tier {info.get('tier','free')}[/dim]")
    console.print(f"Balance: [cyan]{info.get('balance', 0):,}[/cyan] tokens")
    by_tool = info.get("by_tool", {})
    if by_tool:
        console.print("\n[bold]Recent usage[/bold]")
        for tool, n in sorted(by_tool.items(), key=lambda kv: -kv[1]):
            console.print(f"  {tool:<12} {n:>10,} tokens")
    if info.get("balance", 0) <= 0:
        console.print(f"\n[yellow]Out of tokens.[/yellow] Top up at {DEFAULT_BUY_URL}")


@main.command()
@click.argument("code")
def redeem(code):
    """Redeem a free-token code."""
    try:
        result = redeem_code(code)
    except AccountError as exc:
        err.print(f"[bold red]{exc}[/bold red]")
        sys.exit(2)
    console.print(f"[green]Redeemed[/green] {result.get('granted', 0):,} tokens · "
                 f"new balance [cyan]{result.get('balance', 0):,}[/cyan]")


@main.command()
def logout():
    """Remove the saved token from this machine."""
    if clear():
        console.print("[green]Logged out.[/green] Token removed.")
    else:
        console.print("[dim]No token was saved.[/dim]")


@main.command()
def whoami():
    """Show the currently logged-in account."""
    creds = load()
    if not creds:
        console.print("[yellow]Not logged in.[/yellow] Run: oneport-account login <token>")
        return
    console.print(f"[bold]{creds.email or '(unknown email)'}[/bold]  "
                 f"[dim]token {creds.token[:12]}…[/dim]")


if __name__ == "__main__":
    main()

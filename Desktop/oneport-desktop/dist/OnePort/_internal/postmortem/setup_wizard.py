import os
import sys
from pathlib import Path
from rich.console import Console
from rich.rule import Rule
from rich.prompt import Prompt, Confirm

console = Console()

CONFIG_PATH = Path.home() / ".oneport" / "config"


def _write_config(values: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for k, v in values.items():
        if v:
            lines.append(f"{k}={v}")
    CONFIG_PATH.write_text("\n".join(lines) + "\n")


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    config = {}
    for line in CONFIG_PATH.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            config[k.strip()] = v.strip()
    return config


def _validate_gemini_key(key: str) -> bool:
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents="Reply with only the word: ok",
            config=types.GenerateContentConfig(max_output_tokens=5),
        )
        return bool(response.text.strip())
    except Exception:
        return False

def _validate_jira(base_url, email, token, project_key):
    try:
        import requests
        from requests.auth import HTTPBasicAuth
        auth = HTTPBasicAuth(email, token)
        res = requests.get(
            f"{base_url.rstrip('/')}/rest/api/3/project/{project_key}",
            auth=auth,
            headers={"Accept": "application/json"},
            timeout=8,
        )
        if res.status_code == 200:
            return True, res.json().get("name", project_key)
        elif res.status_code == 401:
            return False, "invalid credentials"
        elif res.status_code == 404:
            return False, f"project '{project_key}' not found"
        else:
            return False, f"HTTP {res.status_code}"
    except Exception as e:
        return False, str(e)


def _validate_confluence(base_url, email, token, space_key):
    try:
        import requests
        from requests.auth import HTTPBasicAuth
        auth = HTTPBasicAuth(email, token)
        res = requests.get(
            f"{base_url.rstrip('/')}/rest/api/space/{space_key}",
            auth=auth,
            headers={"Accept": "application/json"},
            timeout=8,
        )
        if res.status_code == 200:
            return True, res.json().get("name", space_key)
        elif res.status_code == 401:
            return False, "invalid credentials"
        elif res.status_code == 404:
            return False, f"space '{space_key}' not found"
        else:
            return False, f"HTTP {res.status_code}"
    except Exception as e:
        return False, str(e)


def run_setup(reconfigure: bool = False):
    existing = _read_config()

    console.print()
    console.print(Rule("[bold yellow]ONEPORT — post-mortem setup[/bold yellow]", style="yellow"))
    console.print()

    if existing and not reconfigure:
        console.print("  [dim]Existing config found at ~/.oneport/config[/dim]")
        console.print("  [dim]Run [cyan]postmortem setup --reconfigure[/cyan] to change it.[/dim]")
        console.print()
        _print_current_config(existing)
        return

    config = {}

    # ── Step 1: Gemini API key ─────────────────────────────────────────────
    console.print("  [bold white]Step 1 of 3 — Gemini API key[/bold white]")
    console.print("  [dim]Get a free key at: https://aistudio.google.com/app/apikey[/dim]")
    console.print("  [dim]No credit card required. 1,500 requests/day free.[/dim]")
    console.print()

    while True:
        key = Prompt.ask(
            "  Enter your Gemini API key",
            default=existing.get("GEMINI_API_KEY", ""),
            password=True,
            console=console,
        )
        if not key:
            console.print("  [red]API key is required.[/red]")
            continue

        console.print("  [dim]Validating key...[/dim]", end="")
        valid = _validate_gemini_key(key)

        if valid:
            console.print("\r  [green]✓ Gemini API key is valid.[/green]          ")
            config["GEMINI_API_KEY"] = key
            break
        else:
            console.print("\r  [red]✗ Key validation failed. Check the key and try again.[/red]")
            retry = Confirm.ask("  Try a different key?", default=True, console=console)
            if not retry:
                console.print("  [dim]Skipping validation — key saved as-is.[/dim]")
                config["GEMINI_API_KEY"] = key
                break

    console.print()

    # ── Step 2: Jira (optional) ────────────────────────────────────────────
    console.print("  [bold white]Step 2 of 3 — Jira integration[/bold white] [dim](optional)[/dim]")
    console.print("  [dim]Automatically creates tickets for action items.[/dim]")
    console.print()

    setup_jira = Confirm.ask(
        "  Set up Jira integration?",
        default=bool(existing.get("JIRA_BASE_URL")),
        console=console,
    )

    if setup_jira:
        jira_url   = Prompt.ask("  Jira base URL",    default=existing.get("JIRA_BASE_URL",   "https://yourcompany.atlassian.net"), console=console)
        jira_email = Prompt.ask("  Jira email",       default=existing.get("JIRA_EMAIL",       ""), console=console)
        jira_token = Prompt.ask("  Jira API token",   default=existing.get("JIRA_API_TOKEN",   ""), password=True, console=console)
        jira_proj  = Prompt.ask("  Jira project key", default=existing.get("JIRA_PROJECT_KEY", "ENG"), console=console)

        if jira_email and jira_token:
            console.print("  [dim]Testing Jira connection...[/dim]", end="")
            ok, msg = _validate_jira(jira_url, jira_email, jira_token, jira_proj)
            if ok:
                console.print(f"\r  [green]✓ Jira connected — project: {msg}[/green]          ")
            else:
                console.print(f"\r  [yellow]⚠ Jira check failed: {msg} — saved anyway.[/yellow]")

        config["JIRA_BASE_URL"]    = jira_url
        config["JIRA_EMAIL"]       = jira_email
        config["JIRA_API_TOKEN"]   = jira_token
        config["JIRA_PROJECT_KEY"] = jira_proj
    else:
        console.print("  [dim]Skipping Jira.[/dim]")

    console.print()

    # ── Step 3: Confluence (optional) ─────────────────────────────────────
    console.print("  [bold white]Step 3 of 3 — Confluence integration[/bold white] [dim](optional)[/dim]")
    console.print("  [dim]Publishes post-mortems directly to your Confluence space.[/dim]")
    console.print()

    setup_conf = Confirm.ask(
        "  Set up Confluence integration?",
        default=bool(existing.get("CONFLUENCE_BASE_URL")),
        console=console,
    )

    if setup_conf:
        conf_url    = Prompt.ask("  Confluence base URL",  default=existing.get("CONFLUENCE_BASE_URL",       "https://yourcompany.atlassian.net/wiki"), console=console)
        conf_email  = Prompt.ask("  Confluence email",     default=existing.get("CONFLUENCE_EMAIL",           ""), console=console)
        conf_token  = Prompt.ask("  Confluence API token", default=existing.get("CONFLUENCE_API_TOKEN",       ""), password=True, console=console)
        conf_space  = Prompt.ask("  Confluence space key", default=existing.get("CONFLUENCE_SPACE_KEY",       "ENG"), console=console)
        conf_parent = Prompt.ask("  Parent page ID",       default=existing.get("CONFLUENCE_PARENT_PAGE_ID", ""), console=console)

        if conf_email and conf_token:
            console.print("  [dim]Testing Confluence connection...[/dim]", end="")
            ok, msg = _validate_confluence(conf_url, conf_email, conf_token, conf_space)
            if ok:
                console.print(f"\r  [green]✓ Confluence connected — space: {msg}[/green]          ")
            else:
                console.print(f"\r  [yellow]⚠ Confluence check failed: {msg} — saved anyway.[/yellow]")

        config["CONFLUENCE_BASE_URL"]       = conf_url
        config["CONFLUENCE_EMAIL"]          = conf_email
        config["CONFLUENCE_API_TOKEN"]      = conf_token
        config["CONFLUENCE_SPACE_KEY"]      = conf_space
        config["CONFLUENCE_PARENT_PAGE_ID"] = conf_parent
    else:
        console.print("  [dim]Skipping Confluence.[/dim]")

    console.print()

    # ── Save ──────────────────────────────────────────────────────────────
    _write_config(config)

    console.print(Rule(style="dim"))
    console.print()
    console.print("  [bold green]✓ Setup complete.[/bold green]")
    console.print(f"  [dim]Config saved to: {CONFIG_PATH}[/dim]")
    console.print()
    console.print("  Run your first post-mortem:")
    console.print("  [cyan]  postmortem generate --logs your_incident.log[/cyan]")
    console.print()


def _print_current_config(config: dict):
    console.print("  [bold white]Current configuration[/bold white]")
    console.print()

    def masked(val: str) -> str:
        if not val:
            return "[dim]not set[/dim]"
        if len(val) <= 8:
            return "****"
        return val[:4] + "****" + val[-4:]

    rows = [
        ("Gemini API key",   masked(config.get("GEMINI_API_KEY", ""))),
        ("Jira URL",         config.get("JIRA_BASE_URL")        or "[dim]not set[/dim]"),
        ("Jira project",     config.get("JIRA_PROJECT_KEY")     or "[dim]not set[/dim]"),
        ("Confluence URL",   config.get("CONFLUENCE_BASE_URL")  or "[dim]not set[/dim]"),
        ("Confluence space", config.get("CONFLUENCE_SPACE_KEY") or "[dim]not set[/dim]"),
    ]

    for label, value in rows:
        console.print(f"  [dim]{label:<22}[/dim]  {value}")

    console.print()


def load_config_to_env():
    """
    Called at CLI startup — loads ~/.oneport/config into os.environ
    so all other commands pick up the values automatically.
    Only sets values that aren't already in the environment,
    so explicit env vars always take priority.
    """
    config = _read_config()
    for k, v in config.items():
        if v and not os.environ.get(k):
            os.environ[k] = v
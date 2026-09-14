"""
One-command install into Claude Code: MCP server registration + the write-guard hook.

Writes two things for a project:

  .mcp.json                  registers the `oneport` MCP server (project-scoped,
                             checked into the repo so the whole team gets it)
  .claude/settings.json      registers the PreToolUse guard hook, so Claude Code
                             is BLOCKED from writing a secret before it lands

Both writes are merges — existing servers/hooks/settings are preserved, and
re-running is idempotent (no duplicate entries). With --global-hooks the guard
goes to ~/.claude/settings.json instead, covering every project on the machine.
"""
from __future__ import annotations

import json
from pathlib import Path

GUARD_COMMAND = "oneport-mcp guard"
_HOOK_MATCHER = "Write|Edit|MultiEdit"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        # Never destroy a file we couldn't parse — refuse instead.
        raise RuntimeError(f"{path} exists but is not valid JSON — fix it first.")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def install_mcp_json(project_dir: Path) -> tuple[Path, bool]:
    """Merge the oneport server into <project>/.mcp.json. Returns (path, changed)."""
    path = Path(project_dir) / ".mcp.json"
    data = _load_json(path)
    servers = data.setdefault("mcpServers", {})
    entry = {"command": "oneport-mcp", "args": ["serve"]}
    if servers.get("oneport") == entry:
        return path, False
    servers["oneport"] = entry
    _write_json(path, data)
    return path, True


def _has_guard(hook_groups: list) -> bool:
    for group in hook_groups:
        for hook in (group or {}).get("hooks", []):
            if GUARD_COMMAND in str(hook.get("command", "")):
                return True
    return False


def install_guard_hook(settings_path: Path) -> tuple[Path, bool]:
    """Merge the PreToolUse write-guard into a Claude settings file. Idempotent."""
    data = _load_json(settings_path)
    hooks = data.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    if _has_guard(pre):
        return settings_path, False
    pre.append({
        "matcher": _HOOK_MATCHER,
        "hooks": [{"type": "command", "command": GUARD_COMMAND}],
    })
    _write_json(settings_path, data)
    return settings_path, True


def install_claude(project_dir: str | Path = ".", global_hooks: bool = False) -> list[str]:
    """Full Claude Code install. Returns human-readable lines describing what happened."""
    project = Path(project_dir).resolve()
    notes: list[str] = []

    path, changed = install_mcp_json(project)
    notes.append(f"{'wrote' if changed else 'already configured'}  {path}  (MCP server 'oneport')")

    settings = (Path.home() / ".claude" / "settings.json") if global_hooks \
        else (project / ".claude" / "settings.json")
    path, changed = install_guard_hook(settings)
    scope = "all projects" if global_hooks else "this project"
    notes.append(f"{'wrote' if changed else 'already configured'}  {path}  (write-guard hook, {scope})")

    notes.append("restart Claude Code (or run /mcp) to pick up the server.")
    return notes

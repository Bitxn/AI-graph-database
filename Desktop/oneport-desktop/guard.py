"""guard.py — OnePort Guard: make a repo one that CANNOT ship a secret.

Turns OnePort from a destination app (you remember to open it) into infrastructure
(it sits in the write path). Installs up to three reversible enforcement layers:

  1. git pre-commit hook  — blocks any commit that STAGES a secret
     (`oneport-secrets scan --staged`). The literal "impossible to commit a leak".
  2. Claude Code hook      — after every AI edit, scans for secrets and feeds the
     result back to the agent so it fixes itself (.claude/settings.json).
  3. CI workflow + badge   — a GitHub Action that runs the gates on every PR, plus
     a "Gated by OnePort" README badge (the viral loop).

Everything is marked so we never clobber the user's own hooks/config, and every
layer uninstalls cleanly (the git layer even restores a pre-existing hook).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

MARK = "OnePort Guard"
BADGE_MARK = "<!-- oneport-guard-badge -->"


# ── how a hook invokes the bundled scanner (frozen exe vs dev console script) ──
def _secrets_prefix() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" __tool oneport-secrets'
    return f'"{shutil.which("oneport-secrets") or "oneport-secrets"}"'


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── layer 1: git pre-commit ───────────────────────────────────────────────────
_PRECOMMIT = """#!/bin/sh
# {mark} — blocks commits that stage a secret. Managed by OnePort; toggle in the app.
{cmd} scan --staged -f inline
if [ $? -ne 0 ]; then
  echo ""
  echo "  [X] OnePort Guard blocked this commit: a secret is in your staged changes."
  echo "      Remove and rotate it, or bypass once with:  git commit --no-verify"
  exit 1
fi
exit 0
"""


def _hooks_dir(root: str) -> Path:
    return Path(root) / ".git" / "hooks"


def git_installed(root: str) -> bool:
    pc = _hooks_dir(root) / "pre-commit"
    return pc.exists() and MARK in _read(pc)


def install_git(root: str) -> dict:
    if not (Path(root) / ".git").exists():
        return {"ok": False, "error": "not a git repo — import or clone it first"}
    hd = _hooks_dir(root)
    hd.mkdir(parents=True, exist_ok=True)
    pc = hd / "pre-commit"
    # preserve a pre-existing (non-OnePort) hook so we never destroy the user's work
    if pc.exists() and MARK not in _read(pc):
        backup = hd / "pre-commit.pre-oneport"
        if not backup.exists():
            try:
                pc.rename(backup)
            except OSError:
                pass
    body = _PRECOMMIT.format(mark=MARK, cmd=_secrets_prefix())
    # write bytes with LF endings — a CRLF shebang breaks /bin/sh on Windows git
    pc.write_bytes(body.replace("\r\n", "\n").encode("utf-8"))
    try:
        os.chmod(pc, 0o755)
    except OSError:
        pass
    return {"ok": True}


def uninstall_git(root: str) -> dict:
    hd = _hooks_dir(root)
    pc = hd / "pre-commit"
    if pc.exists() and MARK in _read(pc):
        try:
            pc.unlink()
        except OSError:
            pass
        backup = hd / "pre-commit.pre-oneport"
        if backup.exists():
            try:
                backup.rename(pc)
            except OSError:
                pass
    return {"ok": True}


# ── layer 2: Claude Code hook ─────────────────────────────────────────────────
def _claude_cmd() -> str:
    return (f"{_secrets_prefix()} scan . -f inline --no-triage || "
            f"(echo 'OnePort Guard: a secret was detected in your edit — remove and "
            f"rotate it before continuing.' >&2; exit 2)")


def _is_oneport_hook(entry: dict) -> bool:
    for h in (entry.get("hooks") or []):
        if "oneport" in str(h.get("command", "")).lower():
            return True
    return False


def claude_installed(root: str) -> bool:
    data = _read_json(Path(root) / ".claude" / "settings.json") or {}
    post = (data.get("hooks") or {}).get("PostToolUse") or []
    return any(_is_oneport_hook(e) for e in post)


def install_claude(root: str) -> dict:
    d = Path(root) / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "settings.json"
    data = _read_json(f) or {}
    hooks = data.setdefault("hooks", {})
    post = hooks.setdefault("PostToolUse", [])
    post[:] = [e for e in post if not _is_oneport_hook(e)]   # de-dupe our own
    post.append({"matcher": "Edit|Write|MultiEdit",
                 "hooks": [{"type": "command", "command": _claude_cmd()}]})
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True}


def uninstall_claude(root: str) -> dict:
    f = Path(root) / ".claude" / "settings.json"
    data = _read_json(f)
    if not data:
        return {"ok": True}
    post = (data.get("hooks") or {}).get("PostToolUse")
    if isinstance(post, list):
        post[:] = [e for e in post if not _is_oneport_hook(e)]
        if not post:
            data["hooks"].pop("PostToolUse", None)
        if not data.get("hooks"):
            data.pop("hooks", None)
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True}


# ── layer 3: CI workflow + README badge ───────────────────────────────────────
_CI_YML = """# Managed by OnePort Guard — runs the pre-ship gates on every PR.
name: OnePort Guard
on:
  pull_request:
  push:
    branches: [ main, master ]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install oneport-secrets oneport-depcheck
      - name: Secret scan
        run: oneport-secrets scan . -f inline
      - name: Dependency CVEs
        run: oneport-depcheck scan . -f inline
"""

_BADGE = ("[![Gated by OnePort](https://img.shields.io/badge/gated%20by-OnePort-018c37)]"
          "(https://github.com/Bitxn/Oneport-Desktop) " + BADGE_MARK)


def _ci_path(root: str) -> Path:
    return Path(root) / ".github" / "workflows" / "oneport.yml"


def ci_installed(root: str) -> bool:
    return _ci_path(root).exists()


def _readme(root: str) -> Path | None:
    for name in ("README.md", "Readme.md", "readme.md"):
        p = Path(root) / name
        if p.exists():
            return p
    return None


def badge_installed(root: str) -> bool:
    r = _readme(root)
    return bool(r and BADGE_MARK in _read(r))


def install_ci(root: str) -> dict:
    p = _ci_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_CI_YML, encoding="utf-8")
    # badge: drop it right after the first heading (or at the very top)
    r = _readme(root) or (Path(root) / "README.md")
    text = _read(r)
    if BADGE_MARK not in text:
        if text:
            lines = text.splitlines()
            insert = 1 if lines and lines[0].startswith("#") else 0
            lines.insert(insert, ("\n" + _BADGE) if insert else (_BADGE + "\n"))
            r.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            r.write_text(_BADGE + "\n", encoding="utf-8")
    return {"ok": True}


def uninstall_ci(root: str) -> dict:
    p = _ci_path(root)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass
    r = _readme(root)
    if r and BADGE_MARK in _read(r):
        kept = [ln for ln in _read(r).splitlines() if BADGE_MARK not in ln]
        r.write_text("\n".join(kept).strip() + "\n", encoding="utf-8")
    return {"ok": True}


# ── orchestration ─────────────────────────────────────────────────────────────
LAYERS = ("git", "claude", "ci")


def status(root: str) -> dict:
    return {
        "is_git": (Path(root) / ".git").exists(),
        "git": git_installed(root),
        "claude": claude_installed(root),
        "ci": ci_installed(root),
        "badge": badge_installed(root),
        "protected": git_installed(root) or claude_installed(root) or ci_installed(root),
    }


def install(root: str, layers=None) -> dict:
    layers = layers or list(LAYERS)
    out = {}
    if "git" in layers:
        out["git"] = install_git(root)
    if "claude" in layers:
        out["claude"] = install_claude(root)
    if "ci" in layers:
        out["ci"] = install_ci(root)
    out["status"] = status(root)
    out["ok"] = True
    return out


def uninstall(root: str) -> dict:
    uninstall_git(root)
    uninstall_claude(root)
    uninstall_ci(root)
    return {"ok": True, "status": status(root)}

"""
autofix.py — drive an installed coding agent to fix a failing gate, live.

When a gate blocks/errors, OnePort hands the agent (Claude Code today, Codex when
installed) a tight, grounded instruction — the gate, its findings, the relevant
files — and runs it HEADLESS in the repo, streaming everything into the in-app
terminal ("calling claude… fixing… FIXED"). Then it re-scans to prove the fix.

The agent edits real files, so this only runs when the user explicitly asks, and
the prompt tells it to make the MINIMAL change and touch nothing unrelated.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading

import chat

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _claude_cmd(prompt: str) -> list[str]:
    # Headless print mode, auto-accepting edits so it can actually change files.
    return ["claude", "-p", prompt, "--permission-mode", "acceptEdits"]


def _codex_cmd(prompt: str) -> list[str]:
    return ["codex", "exec", "--full-auto", prompt]


AGENTS = {"claude": _claude_cmd, "codex": _codex_cmd}


def _find_git_bash() -> str | None:
    """Claude Code on Windows needs a bash. Honor an existing setting, else
    derive it from the git install (which may live anywhere, e.g. C:\\Git)."""
    existing = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if existing and os.path.exists(existing):
        return existing
    cands = []
    git = shutil.which("git")
    if git:
        base = os.path.dirname(os.path.dirname(git))   # …/cmd/git.exe → git root
        cands += [os.path.join(base, "bin", "bash.exe"),
                  os.path.join(base, "usr", "bin", "bash.exe")]
    cands += [r"C:\Git\usr\bin\bash.exe", r"C:\Git\bin\bash.exe",
              r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files\Git\usr\bin\bash.exe",
              r"C:\Program Files (x86)\Git\bin\bash.exe"]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def _agent_env(agent: str) -> dict:
    env = os.environ.copy()
    if agent == "claude":
        bash = _find_git_bash()
        if bash:
            env["CLAUDE_CODE_GIT_BASH_PATH"] = bash
        # ── Session isolation (critical) ──────────────────────────────────
        # Claude allows ONE active login session. If OnePort's claude used the
        # user's main login, it would bump their real Claude Code ("open in a
        # different device"). Point it at its OWN config dir so it never reads
        # or touches ~/.claude, and authenticate strictly via the API key.
        try:
            import os as _os
            from pathlib import Path as _Path
            cfg = _Path(_os.environ.get("APPDATA") or _Path.home()) / "OnePort" / "claude-config"
            cfg.mkdir(parents=True, exist_ok=True)
            env["CLAUDE_CONFIG_DIR"] = str(cfg)
        except Exception:
            pass
        try:
            import settings
            key = settings.anthropic_key()
            if key:
                env["ANTHROPIC_API_KEY"] = key
        except Exception:
            pass
    return env


def detect_agent(preferred: str | None = None) -> str | None:
    order = ([preferred] if preferred else []) + [a for a in AGENTS if a != preferred]
    for name in order:
        if name in AGENTS and shutil.which(name):
            return name
    return None


def _prompt(project: dict, gate_entry: dict, manifest: dict) -> str:
    files = manifest.get("files", {})
    diff = chat._current_diff(project["path"])
    changed = chat._changed_files(diff)
    fsum = "\n".join(f"- {r} [{files[r].get('role')}] {files[r].get('summary')}"
                     for r in changed if r in files)[:1500]
    data = json.dumps(gate_entry.get("data") or {})[:1800]
    return (
        "A pre-ship security gate is FAILING in this repository and you must fix it.\n\n"
        f"GATE: {gate_entry.get('title')}\n"
        f"STATUS: {gate_entry.get('summary')}\n"
        f"FINDINGS (JSON): {data}\n\n"
        f"RELEVANT FILES:\n{fsum or '(inspect the working-tree diff)'}\n\n"
        "TASK: Make the MINIMAL code change that fixes this specific finding. Do "
        "not touch unrelated code and do not add features. When done, print one "
        "line starting with 'FIXED:' summarising exactly what you changed."
    )


def _guide(term, agent: str, gate_title: str, output: str) -> None:
    """The agent couldn't complete — ask the AI to explain and guide the user,
    live in the terminal, instead of leaving them with a raw error."""
    term.emit("→ that didn't complete. Asking OnePort AI how to fix it…", "sys")
    try:
        import diagnose
        import settings
        model, gkey = settings.ai_opts()
        res = diagnose.explain(
            f"Running the '{agent}' coding agent to auto-fix the '{gate_title}' "
            f"security gate in a Windows desktop app failed.",
            output, model=model, gemini_key=gkey)
    except Exception as exc:
        res = {"error": str(exc)}
    if res.get("error"):
        term.emit(f"→ (couldn't reach the AI helper: {res['error']})", "err")
        term.emit("→ Most likely: sign in to Claude Code. Open a REAL terminal "
                  "(PowerShell), run `claude`, then `/login` — or add an Anthropic "
                  "API key in Settings. Then click Fix with AI again.", "err")
        return
    term.emit("", "out")
    term.emit("──────── How to fix this ────────", "sys")
    for line in res["guidance"].split("\n"):
        term.emit(line, "out")
    term.emit("─────────────────────────────────", "sys")


def run_fix(project: dict, gate_entry: dict, manifest: dict, term, rescan,
            agent: str | None = None) -> dict:
    agent = detect_agent(agent)
    if not agent:
        term.emit("→ No coding agent found. Install Claude Code (`claude`) or "
                  "Codex to enable auto-fix.", "err")
        return {"ok": False, "error": "no coding agent installed"}

    # Claude runs in an isolated session (see _agent_env) so it never disturbs
    # the user's own Claude Code. That isolated session has no interactive login,
    # so it needs an API key to work — ask for it up front instead of failing.
    if agent == "claude":
        try:
            import settings
            has_key = bool(settings.anthropic_key())
        except Exception:
            has_key = False
        if not has_key:
            term.emit("→ Fix with AI runs Claude Code in an ISOLATED session so it "
                      "won't disturb your own Claude. Add your Anthropic API key in "
                      "Settings (console.anthropic.com) to enable it.", "err")
            return {"ok": False, "error": "add your Anthropic API key in Settings"}

    def worker():
        term.emit(f"→ Calling {agent} to fix “{gate_entry.get('title')}” …", "cmd")
        cmd = AGENTS[agent](_prompt(project, gate_entry, manifest))
        captured, failed = [], False
        try:
            proc = subprocess.Popen(
                cmd, cwd=project["path"], stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=_NO_WINDOW,
                env=_agent_env(agent),
            )
            for line in proc.stdout:
                text = line.rstrip("\n")
                captured.append(text)
                term.emit(text, "out")
                low = text.lower()
                if any(m in low for m in ("not logged in", "please run /login",
                                          "error:", "git for windows",
                                          "must be provided")):
                    failed = True
            proc.wait()
        except Exception as exc:
            term.emit(f"→ agent error: {exc}", "err")
            captured.append(str(exc))
            failed = True

        blob = "\n".join(captured).strip()
        if failed or not blob:
            _guide(term, agent, gate_entry.get("title", ""), blob or "(no output)")
            return
        term.emit(f"→ {agent} finished. Re-checking the gate…", "sys")
        try:
            rescan()
        except Exception:
            pass
        term.emit("→ re-scan complete — check the Gates tab for the new verdict.", "sys")

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "agent": agent, "started": True}

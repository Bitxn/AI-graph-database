"""OnePort Desktop — the guidance rulebook.

A tool that errors is our failure to explain, not the user's fault. This maps an
error/skip signature to a plain-English card: what happened, why, and the exact
steps to fix it. Shown PROACTIVELY (the user shouldn't have to dig).

Three layers, in priority order:
  1. BUILTIN rules  — curated, embedded here, so guidance works offline on day one.
  2. REMOTE overlay — a rulebook.json fetched from a URL you control and cached in
     %APPDATA%/OnePort/rulebook.json. Bump its "version" and every installed app
     picks up new/edited rules WITHOUT a rebuild. This is the "self-updating" part.
  3. LEARNED rules  — when nothing matches, the app asks the AI (diagnose.py) and
     caches that answer keyed by the error's signature, so the same error is
     instant next time. Learned rules are clearly marked source="ai" and never
     override a curated rule.

Matching is deterministic substring matching (case-insensitive) — no tokens, no
network needed to get guidance for a known error.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

VERSION = 2
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "OnePort"
USER_PATH = DATA_DIR / "rulebook.json"          # remote overlay + learned rules
LEARNED_PATH = DATA_DIR / "rulebook_learned.json"

# Where the app pulls newer curated rules from. Commit a rulebook.json here with a
# higher "version" and every installed app upgrades its guidance on next launch.
REMOTE_URL = os.environ.get(
    "ONEPORT_RULEBOOK_URL",
    "https://raw.githubusercontent.com/Bitxn/Oneport-Desktop/main/rulebook.json",
)

# ── curated builtin rules ────────────────────────────────────────────────────
# match: any of these substrings (lowercased) present in the error/summary text.
BUILTIN = {
    "version": VERSION,
    "rules": [
        {
            "id": "zip-not-git",
            "match": ["no .git of its own", "not a git repository",
                      "looks like a zip", "use `git clone`", "attach the cloned repo"],
            "title": "This folder isn’t a git repository",
            "why": "It looks like a ZIP download from GitHub (folders named like "
                   "“repo-main”). ZIPs contain no git history, so the history-based "
                   "checks (migrations, API breaks, review, test-gaps) have nothing "
                   "to compare against.",
            "fix": [
                "Delete this project from the sidebar.",
                "Clone the repo instead:  git clone <repo-url>",
                "Attach the cloned folder — now it has real history and every tool runs.",
            ],
            "severity": "info",
        },
        {
            "id": "one-commit",
            "match": ["only one commit", "commit another change",
                      "so there's a diff", "so there’s a diff"],
            "title": "This repo has only one commit",
            "why": "The change-based checks compare your latest commit against the "
                   "previous one. With a single commit there’s no “before” to diff.",
            "fix": [
                "Make one more commit (even a tiny change).",
                "Or stage some edits, then re-run the scan.",
            ],
            "severity": "info",
        },
        {
            "id": "no-commits",
            "match": ["no commits yet", "make a commit to enable"],
            "title": "This repo has no commits yet",
            "why": "History-based checks need at least one commit to look at.",
            "fix": [
                "git add -A && git commit -m \"initial commit\"",
                "Re-run the scan.",
            ],
            "severity": "info",
        },
        {
            "id": "git-missing",
            "match": ["git isn't available", "git isn’t available", "install git"],
            "title": "Git isn’t installed",
            "why": "The history-based checks shell out to git, which isn’t on this "
                   "machine’s PATH.",
            "fix": [
                "Install Git for Windows: https://git-scm.com/download/win",
                "Restart OnePort, then re-run the scan.",
            ],
            "severity": "warn",
        },
        {
            "id": "base-branch",
            "match": ["not a valid object name", "unknown revision",
                      "invalid reference", "ambiguous argument"],
            "title": "The base branch doesn’t exist in this repo",
            "why": "The API-breaks check compares your branch against a base branch "
                   "(default: “main”). This repo’s default branch is probably named "
                   "something else (e.g. “master”).",
            "fix": [
                "Check your default branch:  git branch",
                "Set the project’s base branch to match it (or rename to main).",
            ],
            "severity": "info",
        },
        {
            "id": "dep-timeout",
            "match": ["timed out after", "timeout"],
            "title": "The check timed out",
            "why": "Large repositories (or a slow network) can make the dependency "
                   "scan exceed its time budget — it queries the OSV vulnerability "
                   "database online for every dependency.",
            "fix": [
                "Check your internet connection.",
                "Re-run the scan — results are cached, so a retry is usually faster.",
                "For very large monorepos, scan a single package folder.",
            ],
            "severity": "warn",
        },
        {
            "id": "anthropic-key",
            "match": ["anthropic api key", "fix with ai runs claude",
                      "add your anthropic"],
            "title": "Fix-with-AI needs your Anthropic API key",
            "why": "Auto-fix runs Claude Code in an isolated session so it never "
                   "disturbs your own Claude. That session needs its own API key.",
            "fix": [
                "Open Settings → Anthropic API key.",
                "Paste a key from console.anthropic.com, save, and retry.",
            ],
            "severity": "info",
        },
        {
            "id": "needs-login",
            "match": ["needs oneport login", "oneport-account login",
                      "not logged in"],
            "title": "This AI check needs you to be signed in",
            "why": "The AI-powered gates (code review, test-gaps) use OnePort’s "
                   "managed model, which draws on your account’s token balance.",
            "fix": [
                "Sign in with your access token, or",
                "Add your own Gemini key in Settings to run AI checks on your quota.",
            ],
            "severity": "info",
        },
        {
            "id": "rate-limited",
            "match": ["rate-limit", "rate limited", "429", "try again shortly"],
            "title": "The AI model is rate-limited",
            "why": "The free tier allows a limited number of AI calls per minute.",
            "fix": [
                "Wait about a minute and try again.",
                "Add your own Gemini key in Settings to avoid the shared limit.",
            ],
            "severity": "info",
        },
        {
            "id": "conformance-needs-change",
            "match": ["no intent doc", "intent.md", "no staged changes",
                      "no changes to check", "nothing to check", "no change set"],
            "title": "Intent conformance needs a spec + a change to check",
            "why": "This check compares a code CHANGE against an intent doc (how the "
                   "software should behave). OnePort now auto-drafts the intent doc "
                   "for you — but it still needs git history (a recent commit or "
                   "staged edit) to have something to check.",
            "fix": [
                "Run it on a cloned repo (not a ZIP) so it has git history.",
                "Make or stage a change, then run it again.",
                "Edit .oneport/intent.md to sharpen what 'correct' means for your app.",
            ],
            "severity": "info",
        },
        {
            "id": "not-installed",
            "match": ["not installed", "pip install"],
            "title": "This tool isn’t available",
            "why": "The underlying CLI for this check couldn’t be found. In the "
                   "packaged app every tool is bundled, so this usually means a "
                   "broken install.",
            "fix": [
                "Reinstall OnePort from the latest installer.",
            ],
            "severity": "warn",
        },
    ],
}

_LOCK = threading.Lock()
_CACHE: dict | None = None
_CACHE_AT = 0.0


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _effective() -> dict:
    """Builtin rules, with the remote overlay's rules taking precedence by id, and
    learned (AI) rules appended last (lowest priority)."""
    global _CACHE, _CACHE_AT
    with _LOCK:
        if _CACHE is not None and (time.time() - _CACHE_AT) < 30:
            return _CACHE
        rules = {r["id"]: dict(r, source=r.get("source", "builtin"))
                 for r in BUILTIN["rules"]}
        remote = _read_json(USER_PATH)
        if isinstance(remote, dict) and remote.get("version", 0) >= 0:
            for r in remote.get("rules", []):
                if r.get("id") and r.get("match"):
                    rules[r["id"]] = dict(r, source=r.get("source", "remote"))
        ordered = list(rules.values())
        learned = _read_json(LEARNED_PATH) or {}
        for sig, g in learned.items():
            ordered.append({"id": f"learned:{sig}", "match": [sig],
                            "title": g.get("title", "Guidance"),
                            "why": g.get("why", ""), "fix": g.get("fix", []),
                            "severity": "info", "source": "ai"})
        eff = {"version": max(BUILTIN["version"],
                              (remote or {}).get("version", 0) if isinstance(remote, dict) else 0),
               "rules": ordered}
        _CACHE, _CACHE_AT = eff, time.time()
        return eff


def all_rules() -> dict:
    """The effective rulebook — served to the UI so it can match client-side."""
    return _effective()


def _sig(text: str) -> str:
    """A stable-ish signature for a raw error: strip digits/paths so the same class
    of error maps to one learned rule."""
    t = (text or "").lower()
    t = re.sub(r"[a-z]:\\[^\s\"']+", "<path>", t)
    t = re.sub(r"/[^\s\"']+", "<path>", t)
    t = re.sub(r"\d+", "#", t)
    return t.strip()[:80]


def match(text: str) -> dict | None:
    """First rule whose any `match` substring appears in `text` (case-insensitive)."""
    if not text:
        return None
    low = text.lower()
    for r in _effective()["rules"]:
        for p in r.get("match", []):
            if p and p.lower() in low:
                return {k: r[k] for k in ("id", "title", "why", "fix", "severity", "source")
                        if k in r}
    return None


def learn(text: str, guidance: dict) -> None:
    """Cache an AI-generated guidance for an unseen error, keyed by signature."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = _read_json(LEARNED_PATH) or {}
        data[_sig(text)] = {
            "title": guidance.get("title", "AI guidance"),
            "why": guidance.get("why", ""),
            "fix": guidance.get("fix", []),
        }
        LEARNED_PATH.write_text(json.dumps(data, indent=1), encoding="utf-8")
        with _LOCK:
            global _CACHE
            _CACHE = None  # invalidate so the new rule is picked up
    except Exception:
        pass


def refresh(url: str | None = None, timeout: float = 6.0) -> bool:
    """Best-effort: pull a newer curated rulebook from REMOTE_URL and cache it.
    Only overwrites when the remote 'version' is >= the current cached one. Silent
    on any failure (offline-first — the builtin rules always work)."""
    url = url or REMOTE_URL
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OnePort"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
        if not isinstance(data, dict) or "rules" not in data:
            return False
        cur = _read_json(USER_PATH) or {}
        if int(data.get("version", 0)) < int(cur.get("version", 0)):
            return False
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        USER_PATH.write_text(json.dumps(data, indent=1), encoding="utf-8")
        with _LOCK:
            global _CACHE
            _CACHE = None
        return True
    except Exception:
        return False


def refresh_async() -> None:
    threading.Thread(target=refresh, daemon=True).start()

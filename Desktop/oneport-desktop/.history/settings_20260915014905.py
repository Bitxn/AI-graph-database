"""
settings.py — app-wide preferences, stored in %APPDATA%/OnePort/settings.json.

All AI features (mind-map, chat, fix-bundles) run on the metered OnePort proxy.
There is no bring-your-own-key: users never paste API keys into the app — the
keys live only on the OnePort backend, so a distributed client can never leak
them. Callers still accept a gemini_key argument for API compatibility, but the
app always passes None, routing every AI call through the managed proxy.
"""
from __future__ import annotations

import json

from projects import DATA_DIR

_PATH = DATA_DIR / "settings.json"

DEFAULTS = {
    "model": "gemini-2.5-flash",
    "watch_default": False,     # auto-watch a project when opened
    "ai_default": False,        # include AI gates by default in a scan
}


def load() -> dict:
    data = dict(DEFAULTS)
    if _PATH.exists():
        try:
            data.update({k: v for k, v in json.loads(
                _PATH.read_text(encoding="utf-8")).items() if k in DEFAULTS})
        except (OSError, json.JSONDecodeError):
            pass
    return data


def save(patch: dict) -> dict:
    data = load()
    for k, v in (patch or {}).items():
        if k in DEFAULTS:
            data[k] = v
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


# One source of truth for the model. Hardcoded — the managed proxy runs 2.5 Flash.
GEMINI_MODEL = "gemini-2.5-flash"


def ai_opts() -> tuple[str, str | None]:
    """(model, gemini_key-or-None) for the AI callers.

    Model is HARDCODED to gemini-2.5-flash — the Settings dropdown no longer
    changes it. gemini_key is ALWAYS None: every AI call goes through the OnePort
    managed proxy, so the client holds no keys to leak."""
    return GEMINI_MODEL, None


def anthropic_key() -> str:
    """No client-side Anthropic key. Fix-with-AI relies on a locally
    authenticated `claude` CLI if present; otherwise it's unavailable."""
    return ""


def public() -> dict:
    """Settings for the UI. No keys are ever stored or returned."""
    s = load()
    return {**s,
            "has_key": False, "key_hint": "",
            "has_anthropic": False, "anthropic_hint": ""}

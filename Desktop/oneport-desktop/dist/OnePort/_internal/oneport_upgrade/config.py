"""Configuration — CLI overrides → env → .oneportrc → defaults. A model key is
optional; scan/apply are fully deterministic, only `plan` needs it."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from oneport_upgrade.exceptions import ConfigError

# gemini-flash-latest tracks the current flash model, so it won't 404 when Google
# retires a pinned version (as gemini-2.5-flash was).
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 2048
RC_FILE_NAME = ".oneportrc"
DEFAULT_GUIDELINES_PATH = ".oneport/guidelines.md"


@dataclass
class Config:
    model: str = DEFAULT_GEMINI_MODEL
    api_key: str = ""
    max_tokens: int = DEFAULT_MAX_TOKENS
    output_format: str = "inline"
    guidelines_path: str = DEFAULT_GUIDELINES_PATH
    verify_timeout: int = 600

    @property
    def has_key(self) -> bool:
        """True when a model is actually reachable.

        With managed keys this is "logged in to Oneport", NOT "holds a BYOK key".
        `api_key` is vestigial — llm.py routes every call through the metered
        proxy and ignores it — so gating on it made `upgrade plan` report
        "No model key" and skip the planner for EVERY user, logged in or not.
        """
        from oneport_account import is_logged_in
        return is_logged_in()


def _find_rc(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for d in [current, *current.parents]:
        if (d / RC_FILE_NAME).exists():
            return d / RC_FILE_NAME
    return None


def load_config(config_path: str | Path | None = None,
                overrides: dict[str, Any] | None = None) -> Config:
    data: dict[str, Any] = {}
    rc = Path(config_path) if config_path else _find_rc()
    if rc and rc.exists():
        try:
            loaded = yaml.safe_load(rc.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded.get("upgrade", loaded)
        except yaml.YAMLError as exc:
            raise ConfigError(f"Failed to parse {rc}: {exc}") from exc

    if key := os.getenv("ANTHROPIC_API_KEY"):
        data["api_key"] = key
        data.setdefault("model", DEFAULT_CLAUDE_MODEL)
    elif gem := (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        data["api_key"] = gem
        data.setdefault("model", DEFAULT_GEMINI_MODEL)
    if model := os.getenv("ONEPORT_MODEL"):
        data["model"] = model

    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
    try:
        return Config(**known)
    except TypeError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

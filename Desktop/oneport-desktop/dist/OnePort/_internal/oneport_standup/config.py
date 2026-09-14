"""
Configuration for oneport-standup.

Priority: CLI overrides → env vars → .oneportrc (walked up) → defaults. A model key
is optional — with `--no-llm` (or no key) the tool still produces a deterministic,
commit-grouped report; the model only turns commits into polished prose.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from oneport_standup.exceptions import ConfigError

# `gemini-flash-latest` is the current-flash alias — it won't 404 when Google
# retires a pinned version (as gemini-2.5-flash was), so the default self-updates.
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
    output_format: str = "markdown"
    guidelines_path: str = DEFAULT_GUIDELINES_PATH
    max_commits: int = 120          # cap fed to the narrator to bound tokens

    @property
    def has_key(self) -> bool:
        """Narration is available when logged in to Oneport (the metered proxy),
        not when a local API key is set — standup no longer uses BYOK keys."""
        from oneport_account import is_logged_in

        return is_logged_in()


def _find_rc(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for d in [current, *current.parents]:
        cand = d / RC_FILE_NAME
        if cand.exists():
            return cand
    return None


def load_config(config_path: str | Path | None = None,
                overrides: dict[str, Any] | None = None) -> Config:
    data: dict[str, Any] = {}
    rc = Path(config_path) if config_path else _find_rc()
    if rc and rc.exists():
        try:
            loaded = yaml.safe_load(rc.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded.get("standup", loaded)
        except yaml.YAMLError as exc:
            raise ConfigError(f"Failed to parse {rc}: {exc}") from exc

    # Narration runs through the Oneport managed proxy (Gemini, server-side,
    # metered) — no BYOK key. Only the model name is configurable.
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

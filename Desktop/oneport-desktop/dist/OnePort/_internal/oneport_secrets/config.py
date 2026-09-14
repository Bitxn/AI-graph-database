"""
Configuration for Oneport Secrets.

Priority (highest to lowest):
  1. Explicit overrides (CLI flags)
  2. Environment variables (GEMINI_API_KEY, ONEPORT_MODEL, ...)
  3. .oneportrc (YAML) discovered from cwd upward
  4. Built-in defaults

Custom detector regexes and ignore paths additionally come from
`.oneport/guidelines.md` (see oneport_secrets/guidelines.py).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from oneport_secrets.exceptions import ConfigError

DEFAULT_MODEL = "gemini-flash-latest"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_GUIDELINES_PATH = ".oneport/guidelines.md"


class Config(BaseModel):
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    api_key: str = ""
    # Extra ignore globs beyond guidelines / built-in skip dirs.
    ignore_paths: list[str] = Field(default_factory=list)
    entropy: bool = True
    guidelines_path: str = DEFAULT_GUIDELINES_PATH
    output_format: str = "inline"

    @property
    def has_key(self) -> bool:
        """Triage is available when logged in to Oneport (the metered proxy), not
        when a local API key is set — no BYOK."""
        from oneport_account import is_logged_in

        return is_logged_in()


def _find_rc_file(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / ".oneportrc"
        if candidate.exists():
            return candidate
    return None


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse {path}: {exc}") from exc


def load_config(
    config_path: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
    require_key: bool = False,
) -> Config:
    """Build a Config. When require_key is False (the default) a missing API key
    is allowed — detection is deterministic and runs without a model; triage just
    fails safe (everything blocks)."""
    data: dict[str, Any] = {}

    rc_path = Path(config_path) if config_path else _find_rc_file()
    if rc_path and rc_path.exists():
        data = _load_yaml(rc_path)

    # Triage runs through the Oneport managed proxy (Gemini, server-side,
    # metered) — no BYOK key. Only the model name is configurable.
    data.setdefault("model", DEFAULT_MODEL)
    if model := os.getenv("ONEPORT_MODEL"):
        data["model"] = model

    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})

    try:
        config = Config(**data)
    except Exception as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

    if require_key and not config.has_key:
        raise ConfigError(
            "Not logged in to Oneport. Run `oneport-account login <token>` "
            "(free token at https://oneport.dev) to enable triage."
        )
    return config

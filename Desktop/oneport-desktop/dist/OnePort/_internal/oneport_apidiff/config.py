"""
Configuration loading for Oneport ApiDiff.

Priority (highest to lowest):
  1. Explicit overrides (CLI flags)
  2. Environment variables (ANTHROPIC_API_KEY / GEMINI_API_KEY / ONEPORT_MODEL)
  3. .oneportrc in the current directory or any parent (shared with oneport-review;
     unknown keys are ignored, so one rc file serves the whole suite)
  4. Built-in defaults
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from oneport_apidiff.exceptions import ConfigError

DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-20250514"
# Managed proxy default. gemini-flash-latest tracks the current flash model, so
# it won't 404 when Google retires a pinned version (as gemini-2.5-flash was).
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_MAX_TOKENS = 8192


class Config(BaseModel):
    # .oneportrc is shared with oneport-review — ignore keys we don't own.
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    # Managed by the Oneport proxy — Gemini by default. (No BYOK.)
    model: str = DEFAULT_GEMINI_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    api_key: str = ""
    # Repo-relative path to the team guidelines file injected into the
    # classification prompt (e.g. "internal package — only flag __all__ symbols").
    guidelines_path: str = ".oneport/guidelines.md"
    # Paths whose changes are never treated as public API (glob, ** supported).
    ignore_paths: list[str] = Field(default_factory=list)


def _find_rc_file(start: Path | None = None) -> Path | None:
    """Walk up from start (default: cwd) looking for .oneportrc."""
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
    require_api_key: bool = True,
) -> Config:
    """
    Build a Config by merging defaults → rc file → env vars → overrides.

    Args:
        config_path: Explicit path to .oneportrc. Auto-discovered if None.
        overrides: Dict of keys to override last (e.g. from CLI flags).
        require_api_key: gates the LLM path. With managed keys this no longer
            means "a BYOK key" — it means "logged in to Oneport". False for
            --no-llm runs (deterministic detection needs no account).
    """
    data: dict[str, Any] = {}

    rc_path = Path(config_path) if config_path else _find_rc_file()
    if rc_path:
        data = _load_yaml(rc_path)

    if model := os.getenv("ONEPORT_MODEL"):
        data["model"] = model

    if overrides:
        data.update(overrides)

    try:
        config = Config(**data)
    except Exception as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

    # Managed model: the LLM path needs a logged-in Oneport account, not a key.
    if require_api_key:
        from oneport_account import is_logged_in
        if not is_logged_in():
            raise ConfigError(
                "Not logged in to Oneport. Get free tokens at https://oneport.dev, "
                "then run:  oneport-account login <op_live_...>\n"
                "Or run deterministic-only with --no-llm (no account needed)."
            )

    return config

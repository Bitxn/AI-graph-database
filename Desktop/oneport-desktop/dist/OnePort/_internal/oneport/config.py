"""
Configuration loading for Oneport Review.

Priority (highest to lowest):
  1. Explicit kwargs passed to Reviewer()
  2. Environment variables (ONEPORT_*)
  3. .oneportrc in the current directory or any parent
  4. Built-in defaults
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from oneport.exceptions import ConfigError


# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_MODEL = "gemini-flash-latest"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_FORMAT = "inline"
DEFAULT_MIN_SEVERITY = "warning"
DEFAULT_FAIL_ON = "error"        # severity that fails CI (blocking); != display filter
CACHE_TTL_SECONDS = 86_400  # 24 hours


# ── Pydantic models ────────────────────────────────────────────────────────────

class RulesConfig(BaseModel):
    ignore: list[str] = Field(default_factory=list)
    severity: dict[str, str] = Field(default_factory=dict)


class OutputConfig(BaseModel):
    format: str = DEFAULT_FORMAT
    min_severity: str = DEFAULT_MIN_SEVERITY
    show_snippet: bool = True

    @field_validator("format")
    @classmethod
    def valid_format(cls, v: str) -> str:
        allowed = {"inline", "json", "github", "sarif"}
        if v not in allowed:
            raise ValueError(f"format must be one of {allowed}")
        return v

    @field_validator("min_severity")
    @classmethod
    def valid_severity(cls, v: str) -> str:
        allowed = {"info", "warning", "error", "critical"}
        if v not in allowed:
            raise ValueError(f"min_severity must be one of {allowed}")
        return v


class CacheConfig(BaseModel):
    enabled: bool = True
    ttl: int = CACHE_TTL_SECONDS


class Config(BaseModel):
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    api_key: str = ""
    # Severity that BLOCKS CI (exit 1). Independent of output.min_severity, which
    # only controls what's displayed. Default preserves prior behaviour (error).
    fail_on: str = DEFAULT_FAIL_ON
    rules: RulesConfig = Field(default_factory=RulesConfig)
    ignore_paths: list[str] = Field(default_factory=list)
    output: OutputConfig = Field(default_factory=OutputConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    telemetry: bool = False  # strictly opt-in — see PRIVACY.md
    # Diffs larger than this are split on file boundaries into multiple model
    # calls and merged (see oneport/chunker.py).
    max_chunk_chars: int = 60_000
    # Run ruff/bandit on locally reviewed files (when installed) and feed their
    # findings to the model as verification hints. See oneport/analyzers.py.
    analyzers: bool = True
    # Include the full (line-numbered) content of changed files alongside the
    # diff, so the model sees surrounding code, not just hunks. Skipped for
    # diffs big enough to be chunked.
    full_file_context: bool = True
    max_context_chars: int = 40_000
    # Repo-relative path to the team guidelines file injected into every review
    # prompt. See oneport/guidelines.py.
    guidelines_path: str = ".oneport/guidelines.md"

    @field_validator("fail_on")
    @classmethod
    def valid_fail_on(cls, v: str) -> str:
        allowed = {"info", "warning", "error", "critical"}
        if v not in allowed:
            raise ValueError(f"fail_on must be one of {allowed}")
        return v


# ── Discovery & loading ────────────────────────────────────────────────────────

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
) -> Config:
    """
    Build a Config by merging defaults → rc file → env vars → overrides.

    Args:
        config_path: Explicit path to .oneportrc. Auto-discovered if None.
        overrides: Dict of keys to override last (e.g. from CLI flags).
    """
    data: dict[str, Any] = {}

    # 1. Load from rc file
    rc_path = Path(config_path) if config_path else _find_rc_file()
    if rc_path:
        data = _load_yaml(rc_path)

    # 2. The model runs through the Oneport managed proxy (Gemini, server-side,
    #    metered) — no BYOK key. Only the model name is configurable.
    if model := os.getenv("ONEPORT_MODEL"):
        data["model"] = model

    if fmt := os.getenv("ONEPORT_FORMAT"):
        data.setdefault("output", {})["format"] = fmt

    if telemetry := os.getenv("ONEPORT_TELEMETRY"):
        data["telemetry"] = telemetry.lower() not in {"0", "false", "off", "no"}

    # 3. Apply explicit overrides
    if overrides:
        data.update(overrides)

    try:
        config = Config(**data)
    except Exception as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

    from oneport_account import is_logged_in
    if not is_logged_in():
        raise ConfigError(
            "Not logged in to Oneport. Run `oneport-account login <token>` "
            "(free token at https://oneport.dev) to enable AI review."
        )

    return config

"""
Configuration loading for Oneport Migrate.

Priority (highest to lowest):
  1. Explicit overrides (CLI flags)
  2. Environment variables (ONEPORT_MIGRATE_*)
  3. .oneportmigraterc in the current directory or any parent
  4. Built-in defaults

Unlike oneport-review, a missing API key is NOT a hard error: the
deterministic rule engine works without one. The Checker simply skips the
LLM layer and says so in the output (llm_note).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from oneport_migrate.exceptions import ConfigError


# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_MODEL = "gemini-flash-latest"
# The blast-radius response carries a full multi-step rewrite plan (often with
# embedded code), so it needs headroom — 4096 truncated the JSON mid-plan on
# gemini-2.5-flash, degrading an otherwise-good assessment. 8192 clears it.
DEFAULT_MAX_TOKENS = 8192
DEFAULT_FORMAT = "inline"
DEFAULT_MIN_SEVERITY = "warning"
DEFAULT_DB = "postgres"

RC_FILE_NAME = ".oneportmigraterc"


# ── Pydantic models ────────────────────────────────────────────────────────────

class RulesConfig(BaseModel):
    ignore: list[str] = Field(default_factory=list)
    severity: dict[str, str] = Field(default_factory=dict)


class OutputConfig(BaseModel):
    format: str = DEFAULT_FORMAT
    min_severity: str = DEFAULT_MIN_SEVERITY

    @field_validator("format")
    @classmethod
    def valid_format(cls, v: str) -> str:
        allowed = {"inline", "json", "sarif"}
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


class LLMConfig(BaseModel):
    enabled: bool = True
    # Char budget for migration sources + repo context in the prompt.
    max_context_chars: int = 24_000


class Config(BaseModel):
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    api_key: str = ""
    db: str = DEFAULT_DB
    # Severity at/above which a finding blocks CI (exit 1). "error" (default)
    # blocks error + critical, matching the historical behaviour.
    fail_on: str = "error"
    rules: RulesConfig = Field(default_factory=RulesConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    # Repo-relative path to team guidelines injected into the LLM prompt
    # (e.g. "our users table is small, downgrade lock severity").
    guidelines_path: str = ".oneport/guidelines.md"

    @field_validator("db")
    @classmethod
    def valid_db(cls, v: str) -> str:
        allowed = {"postgres", "mysql", "sqlite"}
        if v not in allowed:
            raise ValueError(f"db must be one of {allowed}")
        return v

    @field_validator("fail_on")
    @classmethod
    def valid_fail_on(cls, v: str) -> str:
        allowed = {"info", "warning", "error", "critical"}
        if v not in allowed:
            raise ValueError(f"fail_on must be one of {allowed}")
        return v


# ── Discovery & loading ────────────────────────────────────────────────────────

def _find_rc_file(start: Path | None = None) -> Path | None:
    """Walk up from start (default: cwd) looking for .oneportmigraterc."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / RC_FILE_NAME
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
        config_path: Explicit path to .oneportmigraterc. Auto-discovered if None.
        overrides: Dict of keys to override last (e.g. from CLI flags).
    """
    data: dict[str, Any] = {}

    # 1. Load from rc file
    rc_path = Path(config_path) if config_path else _find_rc_file()
    if rc_path:
        data = _load_yaml(rc_path)

    # 2. Apply environment variables. The assessment runs through the Oneport
    #    managed proxy (Gemini, server-side, metered) — no BYOK key — so only the
    #    model name is configurable and defaults to gemini-flash-latest.
    if model := os.getenv("ONEPORT_MIGRATE_MODEL"):
        data["model"] = model

    if fmt := os.getenv("ONEPORT_MIGRATE_FORMAT"):
        data.setdefault("output", {})["format"] = fmt

    if db := os.getenv("ONEPORT_MIGRATE_DB"):
        data["db"] = db

    # 3. Apply explicit overrides
    if overrides:
        data.update(overrides)

    try:
        return Config(**data)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

"""
Configuration loading for Oneport Costwatch.

Priority (highest to lowest):
  1. Explicit overrides (e.g. CLI flags)
  2. Environment variables
  3. .oneport-costwatch.yml in the current directory or any parent
  4. Built-in defaults
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from costwatch.exceptions import ConfigError

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_MODEL = "gemini-flash-latest"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_FORMAT = "inline"
DEFAULT_MIN_SEVERITY = "info"

# Cost increase (USD/month) at or above which `analyze --post` treats a PR as
# raising cost and posts the gate comment. Small churn shouldn't nag reviewers.
DEFAULT_POST_THRESHOLD = 5.0

CONFIG_FILENAME = ".oneport-costwatch.yml"
DEFAULT_GUIDELINES_PATH = ".oneport/guidelines.md"


# ── Pydantic model ─────────────────────────────────────────────────────────────

class OutputConfig(BaseModel):
    format: str = DEFAULT_FORMAT
    min_severity: str = DEFAULT_MIN_SEVERITY

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
        allowed = {"info", "warning", "high", "critical"}
        if v not in allowed:
            raise ValueError(f"min_severity must be one of {allowed}")
        return v


class Config(BaseModel):
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    api_key: str = ""
    output: OutputConfig = Field(default_factory=OutputConfig)
    # Glob-ish path fragments to skip while scanning for IaC files.
    ignore_paths: list[str] = Field(default_factory=lambda: [".terraform", "node_modules", ".git"])
    # Repo-relative guidelines file injected into every prompt (e.g.
    # "prod must stay on-demand, ignore spot suggestions there").
    guidelines_path: str = DEFAULT_GUIDELINES_PATH
    # Cost-increase gate threshold for --post, in USD/month.
    post_threshold: float = DEFAULT_POST_THRESHOLD
    # CI budget gate (both opt-in, default off so `analyze` still exits 0):
    #   fail_on — block when any non-waived finding is at/above this severity.
    #   budget  — block when monthly cost (or --post delta) exceeds this USD cap.
    fail_on: str = ""
    budget: float | None = None

    @field_validator("fail_on")
    @classmethod
    def valid_fail_on(cls, v: str) -> str:
        allowed = {"", "info", "warning", "high", "critical"}
        if v not in allowed:
            raise ValueError(f"fail_on must be one of {allowed - {''}} (or unset)")
        return v


# ── Discovery & loading ────────────────────────────────────────────────────────

def _find_config_file(start: Path | None = None) -> Path | None:
    """Walk up from start (default: cwd) looking for the config file."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / CONFIG_FILENAME
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
    """Build a Config by merging defaults → config file → env vars → overrides.

    Set require_api_key=False for commands that don't call the model (e.g.
    deterministic-only pricing), so they never demand a key.
    """
    data: dict[str, Any] = {}

    # 1. Config file
    cfg_path = Path(config_path) if config_path else _find_config_file()
    if cfg_path:
        data = _load_yaml(cfg_path)

    # 2. Environment. Costwatch no longer uses a BYOK model key — the analysis
    #    runs through the Oneport managed proxy (Gemini, server-side, metered), so
    #    only the model name is configurable and it stays a Gemini model.
    if model := os.getenv("COSTWATCH_MODEL"):
        data["model"] = model

    if fmt := os.getenv("COSTWATCH_FORMAT"):
        data.setdefault("output", {})["format"] = fmt

    # 3. Explicit overrides
    if overrides:
        data.update(overrides)

    try:
        config = Config(**data)
    except Exception as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

    # The paid path requires an Oneport login (a per-user token), not an API key.
    if require_api_key:
        from oneport_account import is_logged_in

        if not is_logged_in():
            raise ConfigError(
                "Not logged in to Oneport. Run `oneport-account login <token>` "
                "(get a free token at https://oneport.dev), then retry."
            )

    return config

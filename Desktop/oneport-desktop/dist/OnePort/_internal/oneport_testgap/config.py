"""
Configuration loading for Oneport Testgap.

Priority (highest to lowest):
  1. Explicit overrides (CLI flags)
  2. Environment variables (ONEPORT_*, ANTHROPIC_API_KEY / GEMINI_API_KEY)
  3. `testgap:` section of .oneportrc (shared suite config file)
  4. Top-level keys of .oneportrc (model, api_key, ignore_paths, guidelines_path)
  5. Built-in defaults
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from oneport_testgap.exceptions import ConfigError

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_MODEL = "gemini-flash-latest"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_FORMAT = "inline"
DEFAULT_MIN_RISK = "low"

# Keys shared across the Oneport suite at the top level of .oneportrc.
_SHARED_KEYS = ("model", "api_key", "ignore_paths", "guidelines_path")


# ── Pydantic models ────────────────────────────────────────────────────────────

class OutputConfig(BaseModel):
    format: str = DEFAULT_FORMAT
    min_risk: str = DEFAULT_MIN_RISK

    @field_validator("format")
    @classmethod
    def valid_format(cls, v: str) -> str:
        allowed = {"inline", "json", "sarif"}
        if v not in allowed:
            raise ValueError(f"format must be one of {allowed}")
        return v

    @field_validator("min_risk")
    @classmethod
    def valid_min_risk(cls, v: str) -> str:
        allowed = {"high", "medium", "low"}
        if v not in allowed:
            raise ValueError(f"min_risk must be one of {allowed}")
        return v


class GenerateConfig(BaseModel):
    # One repair round: a failing generated test gets its error output fed back
    # to the model once before being discarded.
    repair_rounds: int = 1
    # Seconds allowed for one generated-test pytest run.
    test_timeout: int = 120
    # Where verified tests land (repo-relative). Never auto-committed.
    output_dir: str = "tests/generated"


class CoverageRunConfig(BaseModel):
    # Extra args appended to the `pytest --cov` baseline run (e.g. ["-m", "not slow"]).
    pytest_args: list[str] = Field(default_factory=list)
    # Seconds allowed for the full baseline test-suite run.
    timeout: int = 600


class Config(BaseModel):
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    api_key: str = ""
    # Risk level that FAILS CI (exit 1). Independent of output.min_risk, which
    # only controls display. Default 'critical' preserves prior behaviour.
    fail_on: str = "critical"
    ignore_paths: list[str] = Field(default_factory=list)
    output: OutputConfig = Field(default_factory=OutputConfig)
    generate: GenerateConfig = Field(default_factory=GenerateConfig)
    coverage: CoverageRunConfig = Field(default_factory=CoverageRunConfig)
    # Repo-relative path to the team guidelines file; testing rules from it are
    # injected into ranking and generation prompts. See guidelines.py.
    guidelines_path: str = ".oneport/guidelines.md"

    @field_validator("fail_on")
    @classmethod
    def valid_fail_on(cls, v: str) -> str:
        allowed = {"critical", "high", "medium", "low"}
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

    The rc file is the suite-wide .oneportrc: shared top-level keys (model,
    api_key, ignore_paths, guidelines_path) apply to every Oneport tool, and a
    `testgap:` section overrides them for this tool only.

    Args:
        config_path: Explicit path to .oneportrc. Auto-discovered if None.
        overrides: Dict of keys to override last (e.g. from CLI flags).
    """
    data: dict[str, Any] = {}

    # 1. Load from rc file: shared top-level keys, then the testgap section.
    rc_path = Path(config_path) if config_path else _find_rc_file()
    if rc_path:
        raw = _load_yaml(rc_path)
        data = {k: raw[k] for k in _SHARED_KEYS if k in raw}
        section = raw.get("testgap")
        if isinstance(section, dict):
            data.update(section)

    # 2. The model runs through the Oneport managed proxy (Gemini, server-side,
    #    metered) — no BYOK key. Only the model name is configurable.
    if model := os.getenv("ONEPORT_MODEL"):
        data["model"] = model

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
            "(free token at https://oneport.dev) to enable the AI layer."
        )

    return config

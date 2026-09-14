"""
Configuration for oneport-impact.

Priority (highest to lowest): CLI overrides → env vars → .oneportrc (yaml, walked
up from cwd) → built-in defaults. A model key is OPTIONAL — every deterministic
engine (call graph, co-change, ownership) runs without one; only the risk-verdict
layer needs it, and `--no-llm` skips that.

The thresholds are config so an enterprise can tune what its gate treats as risky
without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from oneport_impact.exceptions import ConfigError

DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 2048
RC_FILE_NAME = ".oneportrc"
DEFAULT_GUIDELINES_PATH = ".oneport/guidelines.md"


@dataclass
class Thresholds:
    # A changed symbol with at least this many callers is "high fan-in" (wide blast radius).
    fan_in_warn: int = 8
    fan_in_error: int = 25
    # A co-change partner counts as a "companion you should probably also touch"
    # when it changed with the target in >= this fraction of the target's commits…
    cochange_confidence: float = 0.5
    # …and at least this many times (guards against tiny-sample coincidences).
    cochange_min_together: int = 3
    # History depth mined for co-change.
    max_commits: int = 1500


@dataclass
class Config:
    model: str = DEFAULT_GEMINI_MODEL
    api_key: str = ""
    max_tokens: int = DEFAULT_MAX_TOKENS
    guidelines_path: str = DEFAULT_GUIDELINES_PATH
    output_format: str = "inline"
    thresholds: Thresholds = field(default_factory=Thresholds)

    @property
    def has_key(self) -> bool:
        """The risk-verdict layer is available when logged in to Oneport (the
        metered proxy), not when a local API key is set — no BYOK."""
        from oneport_account import is_logged_in

        return is_logged_in()


def _find_rc(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for d in [current, *current.parents]:
        cand = d / RC_FILE_NAME
        if cand.exists():
            return cand
    return None


def load_config(
    config_path: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
    require_key: bool = False,
) -> Config:
    data: dict[str, Any] = {}
    rc = Path(config_path) if config_path else _find_rc()
    if rc and rc.exists():
        try:
            loaded = yaml.safe_load(rc.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded.get("impact", loaded)  # allow a nested [impact] block
        except yaml.YAMLError as exc:
            raise ConfigError(f"Failed to parse {rc}: {exc}") from exc

    # The verdict runs through the Oneport managed proxy (Gemini, server-side,
    # metered) — no BYOK key. Only the model name is configurable.
    data.setdefault("model", DEFAULT_GEMINI_MODEL)
    if model := os.getenv("ONEPORT_MODEL"):
        data["model"] = model

    th = Thresholds(**{k: v for k, v in (data.pop("thresholds", {}) or {}).items()
                       if k in Thresholds.__dataclass_fields__})
    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})

    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__ and k != "thresholds"}
    try:
        config = Config(thresholds=th, **known)
    except TypeError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

    if require_key and not config.has_key:
        raise ConfigError(
            "Not logged in to Oneport for the risk-verdict layer. Run "
            "`oneport-account login <token>` (free token at https://oneport.dev), "
            "or pass --no-llm for facts-only output."
        )
    return config

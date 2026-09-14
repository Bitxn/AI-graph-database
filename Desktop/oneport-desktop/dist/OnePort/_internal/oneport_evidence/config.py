"""
Configuration — CLI overrides → .oneportrc → defaults. No model key: evidence is
deterministic by design (no AI in an audit trail).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from oneport_evidence.exceptions import ConfigError

RC_FILE_NAME = ".oneportrc"


@dataclass
class Config:
    org: str = "Your Organization"
    output_format: str = "pdf"
    classification: str = "CONFIDENTIAL"   # banner on the pack


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
                data = loaded.get("evidence", loaded)
        except yaml.YAMLError as exc:
            raise ConfigError(f"Failed to parse {rc}: {exc}") from exc
    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
    try:
        return Config(**known)
    except TypeError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

"""
Runtime settings for the CLI — defaults, overridable by environment.

Deliberately small: the tool already has a setup wizard writing a `.env` for
integration credentials (Jira/Confluence). This just centralises the handful of
behaviour knobs the v0.2 features add, so defaults live in one place instead of
being hard-coded at the call site (which is how the deprecated model ID ended
up pinned in the CLI).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The managed proxy's current default model. gemini-2.5-flash was retired and
# now 404s — never hard-code a model at a call site again; read it from here.
DEFAULT_MODEL = "gemini-flash-latest"


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    model: str = DEFAULT_MODEL
    redact: bool = True                 # scrub secrets/PII before the model sees data
    ground: bool = True                 # verify timeline timestamps against source
    min_severity: str = "LOW"           # reserved for future filtering

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            model=os.getenv("ONEPORT_POSTMORTEM_MODEL", DEFAULT_MODEL),
            redact=_env_bool("ONEPORT_POSTMORTEM_REDACT", True),
            ground=_env_bool("ONEPORT_POSTMORTEM_GROUND", True),
            min_severity=os.getenv("ONEPORT_POSTMORTEM_MIN_SEVERITY", "LOW").upper(),
        )

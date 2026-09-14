"""
Configuration for oneport-depcheck.

Priority (highest to lowest):
  1. Explicit overrides (CLI flags)
  2. Environment variables
  3. Built-in defaults

The LLM key is only needed for the triage layer. Detection (manifest parsing,
OSV lookups, license checks) is fully deterministic and never touches a model,
so `require_llm=False` lets `scan --no-llm` run key-free.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from oneport_depcheck.exceptions import ConfigError

DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 4096
CACHE_TTL_SECONDS = 86_400  # 24 hours — OSV data changes slowly
DEFAULT_GUIDELINES_PATH = ".oneport/guidelines.md"


@dataclass
class Config:
    model: str = ""
    api_key: str = ""
    max_tokens: int = DEFAULT_MAX_TOKENS
    cache_enabled: bool = True
    cache_ttl: int = CACHE_TTL_SECONDS
    guidelines_path: str = DEFAULT_GUIDELINES_PATH
    # Max usage-evidence snippets per package fed to the triage prompt.
    max_evidence_snippets: int = 12
    ignore_paths: list[str] = field(default_factory=list)


def load_config(
    overrides: dict | None = None,
    require_llm: bool = True,
) -> Config:
    """Build a Config from env vars + overrides.

    The triage layer runs through the Oneport managed proxy (Gemini, server-side,
    metered), so it needs an Oneport login rather than a BYOK key. The model name
    stays configurable and defaults to gemini-flash-latest.
    """
    data: dict = {}
    data["model"] = DEFAULT_GEMINI_MODEL

    if model := os.getenv("ONEPORT_MODEL"):
        data["model"] = model

    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})

    config = Config(**data)

    # Triage requires an Oneport login (a per-user token), not an API key.
    if require_llm:
        from oneport_account import is_logged_in

        if not is_logged_in():
            raise ConfigError(
                "Not logged in to Oneport. Run `oneport-account login <token>` "
                "(get a free token at https://oneport.dev), or run with --no-llm "
                "for detection-only output."
            )

    return config

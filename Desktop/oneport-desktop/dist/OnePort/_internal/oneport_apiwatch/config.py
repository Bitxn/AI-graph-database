"""Configuration: the checks file (oneport-apiwatch.yaml).

A checks file lists endpoints to probe. Secrets never live in the file — auth
headers are pulled from environment variables by name, so the config is safe to
commit and the customer's token stays on their machine / in their CI secrets.

Layout::

    model: gemini-2.5-flash          # optional, only used for --explain
    history_file: .oneport-apiwatch-history.json   # optional, customer-owned state
    defaults:                        # optional, applied to every check
      method: GET
      expect_status: 200
      latency_budget_ms: 2000
      timeout: 10
    checks:
      - name: homepage
        url: https://example.com/health
        method: GET
        expect_status: 200           # int or list of ints, e.g. [200, 204]
        latency_budget_ms: 1500
        timeout: 10
        headers:
          Accept: application/json
        auth_header_env: API_TOKEN   # value read from $API_TOKEN at runtime
        auth_header_name: Authorization
        body:                        # JSON body assertions (optional)
          - path: status             # dot-path into the JSON response
            equals: ok
          - path: data.count
            gt: 0

Priority for the model/key: env vars win, then the file, then defaults.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from oneport_apiwatch.exceptions import ConfigError

# Managed proxy default. gemini-flash-latest tracks the current flash model, so
# it won't 404 when Google retires a pinned version (as gemini-2.5-flash was).
DEFAULT_MODEL = "gemini-flash-latest"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_METHOD = "GET"
DEFAULT_EXPECT_STATUS = 200
DEFAULT_LATENCY_BUDGET_MS = 2000
DEFAULT_TIMEOUT = 10
DEFAULT_HISTORY_FILE = ".oneport-apiwatch-history.json"

# Default config filenames searched in the current directory (in order).
CONFIG_FILENAMES = ("oneport-apiwatch.yaml", "oneport-apiwatch.yml")

# Supported JSON body-assertion operators. Each maps to a comparison in probe.py.
ASSERTION_OPS = ("equals", "not_equals", "contains", "exists", "gt", "lt")


class BodyAssertion(BaseModel):
    """A single JSON-body assertion against a dot-path in the response."""

    path: str
    equals: Any = None
    not_equals: Any = None
    contains: Any = None
    exists: bool | None = None
    gt: float | None = None
    lt: float | None = None

    @model_validator(mode="after")
    def exactly_one_op(self) -> BodyAssertion:
        used = [op for op in ASSERTION_OPS if getattr(self, op) is not None]
        if len(used) != 1:
            raise ValueError(
                f"body assertion on '{self.path}' must use exactly one of "
                f"{ASSERTION_OPS}, got {used or 'none'}"
            )
        return self

    @property
    def op(self) -> str:
        return next(op for op in ASSERTION_OPS if getattr(self, op) is not None)


class Check(BaseModel):
    """One endpoint to probe."""

    name: str
    url: str
    method: str = DEFAULT_METHOD
    expect_status: int | list[int] = DEFAULT_EXPECT_STATUS
    latency_budget_ms: int = DEFAULT_LATENCY_BUDGET_MS
    timeout: int = DEFAULT_TIMEOUT
    headers: dict[str, str] = Field(default_factory=dict)
    auth_header_env: str | None = None
    auth_header_name: str = "Authorization"
    body: list[BodyAssertion] = Field(default_factory=list)
    # -- reliability (flap tolerance) --
    # Retry a probe this many times on a TRANSIENT failure (timeout, connection
    # error, or 5xx) before recording it as failed. Assertion/4xx/latency
    # failures are deterministic and never retried.
    retries: int = 0
    retry_backoff_ms: int = 200
    # Consecutive failed runs required before this check "trips" (fails the gate
    # and alerts). 1 = trip on the first failure (the historical behaviour);
    # 3 = ride out two transient blips before paging.
    failure_threshold: int = 1
    # -- maintenance mute --
    muted: bool = False               # hard mute — never trips or alerts
    mute_until: str = ""              # ISO datetime/date; muted only until then
    mute_reason: str = ""

    @field_validator("method")
    @classmethod
    def upper_method(cls, v: str) -> str:
        v = v.upper()
        allowed = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
        if v not in allowed:
            raise ValueError(f"method must be one of {sorted(allowed)}")
        return v

    @field_validator("url")
    @classmethod
    def valid_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"url must start with http:// or https:// (got {v!r})")
        return v

    @property
    def expected_statuses(self) -> list[int]:
        return [self.expect_status] if isinstance(self.expect_status, int) else self.expect_status

    def resolved_headers(self) -> dict[str, str]:
        """Headers with the auth header injected from the environment.

        Missing env var → the header is omitted (the probe then likely gets a
        401/403, which is a real, reportable failure). We never put the raw token
        in the config or the report.
        """
        headers = dict(self.headers)
        if self.auth_header_env:
            value = os.getenv(self.auth_header_env)
            if value:
                headers[self.auth_header_name] = value
        return headers


class Config(BaseModel):
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    api_key: str = ""
    history_file: str = DEFAULT_HISTORY_FILE
    # Probe up to this many endpoints in parallel (a monitor with 50 endpoints
    # shouldn't take 50 × timeout seconds). Results stay in config order.
    concurrency: int = 8
    checks: list[Check]

    @field_validator("checks")
    @classmethod
    def non_empty_unique(cls, v: list[Check]) -> list[Check]:
        if not v:
            raise ValueError("at least one check is required")
        names = [c.name for c in v]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate check names: {sorted(dupes)}")
        return v


def _find_config(start: Path | None = None) -> Path | None:
    directory = (start or Path.cwd()).resolve()
    for name in CONFIG_FILENAMES:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def _apply_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    """Fold a top-level `defaults:` block into each check that omits the key."""
    defaults = raw.pop("defaults", None) or {}
    if not isinstance(defaults, dict):
        raise ConfigError("`defaults` must be a mapping")
    checks = raw.get("checks")
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict):
                for key, value in defaults.items():
                    check.setdefault(key, value)
    return raw


def load_config(config_path: str | Path | None = None) -> Config:
    """Load and validate the checks file, layering env vars for the model/key.

    The Gemini key is optional here: only `--explain` needs it, and the CLI
    surfaces a clear message at that point if it's missing.
    """
    path = Path(config_path) if config_path else _find_config()
    if path is None:
        raise ConfigError(
            "No checks file found. Create oneport-apiwatch.yaml or pass --config. "
            "See the README for the schema."
        )
    if not path.exists():
        raise ConfigError(f"Checks file not found: {path}")

    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a top-level mapping with a `checks` list")

    raw = _apply_defaults(raw)

    # Env layering: only a Gemini key is used by this tool.
    if key := (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        raw.setdefault("api_key", key)
    if model := os.getenv("APIWATCH_MODEL"):
        raw["model"] = model

    try:
        return Config(**raw)
    except Exception as exc:
        raise ConfigError(f"Invalid configuration in {path}: {exc}") from exc

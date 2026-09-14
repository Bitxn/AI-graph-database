"""
Opt-in anonymous usage telemetry.

What we collect (when telemetry is enabled):
  - CLI command invoked (e.g. "review", "cache clear")
  - Target type: file | github_pr | gitlab_mr | bitbucket_pr | staged | head
  - Number of issues found, broken down by severity
  - Review duration in ms
  - OS platform and Python version
  - Oneport version and model used

What we NEVER collect:
  - File contents, diffs, or any source code
  - File names or paths
  - API keys or tokens
  - Any personally identifiable information

Telemetry is OFF by default. It only activates when BOTH are true:
  - you explicitly opt in:  export ONEPORT_TELEMETRY=on
  - this build ships a real analytics key (source builds never do)

See PRIVACY.md for the full data-flow picture.
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Any

from oneport import __version__


_POSTHOG_API_KEY = "phc_oneport_placeholder"   # Replace with real key at build time
_POSTHOG_HOST = "https://app.posthog.com"


def _is_enabled() -> bool:
    """Strictly opt-in: silent unless the user explicitly turned it on AND a
    real key was injected at build time. A placeholder key means source builds
    never send a network request, opted-in or not."""
    if _POSTHOG_API_KEY == "phc_oneport_placeholder":
        return False
    env = os.getenv("ONEPORT_TELEMETRY", "").lower()
    return env in {"1", "true", "on", "yes"}


def capture(event: str, properties: dict[str, Any] | None = None) -> None:
    """
    Fire-and-forget telemetry event. Silently swallows all errors.

    Args:
        event:      Event name, e.g. "review_completed".
        properties: Additional key-value pairs to attach.
    """
    if not _is_enabled():
        return

    payload: dict[str, Any] = {
        "oneport_version": __version__,
        "python_version": sys.version.split()[0],
        "platform": platform.system(),
        **(properties or {}),
    }

    try:
        import threading
        t = threading.Thread(target=_send, args=(event, payload), daemon=True)
        t.start()
    except Exception:
        pass  # Telemetry must never crash the tool


def _send(event: str, properties: dict[str, Any]) -> None:
    try:
        import httpx
        httpx.post(
            f"{_POSTHOG_HOST}/capture/",
            json={
                "api_key": _POSTHOG_API_KEY,
                "event": event,
                "distinct_id": _get_anonymous_id(),
                "properties": properties,
            },
            timeout=3,
        )
    except Exception:
        pass


def _get_anonymous_id() -> str:
    """
    Return a stable anonymous ID derived from the machine.
    Never contains any PII — just a hash of machine identifiers.
    """
    import hashlib
    import platformdirs
    from pathlib import Path

    id_file = Path(platformdirs.user_config_dir("oneport")) / "anonymous_id"
    if id_file.exists():
        return id_file.read_text().strip()

    raw = f"{platform.node()}:{platform.machine()}:{os.getlogin() if hasattr(os, 'getlogin') else ''}"
    anon_id = hashlib.sha256(raw.encode()).hexdigest()[:32]
    id_file.parent.mkdir(parents=True, exist_ok=True)
    id_file.write_text(anon_id)
    return anon_id


# ── Convenience helpers ────────────────────────────────────────────────────────

def track_review(
    target_type: str,
    issue_counts: dict[str, int],
    elapsed_ms: int,
    model: str,
    cached: bool,
) -> None:
    capture("review_completed", {
        "target_type": target_type,
        "elapsed_ms": elapsed_ms,
        "model": model,
        "cached": cached,
        **{f"issues_{k}": v for k, v in issue_counts.items()},
    })

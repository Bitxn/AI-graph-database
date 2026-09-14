"""telemetry.py — minimal desktop product analytics → PostHog.

Makes the DESKTOP app's DAU/WAU measurable (the web console already reports to
PostHog; the desktop reported nothing). Sends ONLY lightweight usage pings —
event name + the signed-in user's email + app version. NEVER code, repo paths,
file contents, or findings. Honors an opt-out (`telemetry_off` in settings.json)
so it stays true to the local-first pitch.

Events:
  app_open      — once, when the app starts
  app_active    — heartbeat every 5 min while the app is open (this is the DAU/WAU
                  signal: a user counts as active on any day they fire it)
  scan_run      — a scan completed (verdict as a property)
  guard_install — Guard was turned on for a repo
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
import uuid
from pathlib import Path

# Public PostHog PROJECT key — write-only capture key, safe to embed (it's the
# same key already shipped in the website's client bundle).
POSTHOG_KEY = "phc_238op2G9i5a1ZBdi5dAlZgzeUsIaK87LU0fAuzOeyGD"
POSTHOG_URL = "https://us.i.posthog.com/capture/"

_DATA = Path(os.environ.get("APPDATA") or Path.home()) / "OnePort"
_APP_VERSION = "0.2.4"
_did = None            # distinct id — the user's email once known, else a machine id
_lock = threading.Lock()


def _machine_id() -> str:
    """A stable anonymous id for this machine (used before/without login)."""
    p = _DATA / "machine_id"
    try:
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        mid = "anon-" + uuid.uuid4().hex[:16]
        _DATA.mkdir(parents=True, exist_ok=True)
        p.write_text(mid, encoding="utf-8")
        return mid
    except OSError:
        return "anon-unknown"


def _enabled() -> bool:
    try:
        data = json.loads((_DATA / "settings.json").read_text(encoding="utf-8"))
        return not data.get("telemetry_off", False)
    except Exception:
        return True


def set_user(email: str | None) -> None:
    global _did
    if email:
        with _lock:
            _did = email


def capture(event: str, props: dict | None = None) -> None:
    """Fire-and-forget a single event to PostHog. Never blocks, never raises."""
    if not _enabled():
        return

    def worker():
        try:
            did = _did or _machine_id()
            body = json.dumps({
                "api_key": POSTHOG_KEY,
                "event": event,
                "distinct_id": did,
                "properties": {
                    **(props or {}),
                    "surface": "desktop",
                    "app_version": _APP_VERSION,
                    "$lib": "oneport-desktop",
                },
            }).encode("utf-8")
            req = urllib.request.Request(
                POSTHOG_URL, data=body,
                headers={"Content-Type": "application/json", "User-Agent": "OnePort"})
            urllib.request.urlopen(req, timeout=6).read()
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def start_heartbeat(interval: int = 300) -> None:
    """Emit `app_active` now and every `interval` seconds while the app runs —
    this is what makes DAU/WAU accurate (an open, in-use app keeps checking in)."""
    def loop():
        while True:
            capture("app_active")
            time.sleep(interval)
    capture("app_open")
    threading.Thread(target=loop, daemon=True).start()

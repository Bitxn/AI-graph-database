"""
Flap gating, mutes, and alert de-duplication — the layer that makes the monitor
trustworthy instead of noisy.

A raw probe result says "this run passed/failed". That alone pages you at 3am for
one transient blip and pages you again on every cron tick while an endpoint is
down. This module turns raw results into an operational verdict using the local
history:

  * a check only "trips" after `failure_threshold` CONSECUTIVE failed runs;
  * a muted check (maintenance window) never trips;
  * an alert fires once when a check newly trips (`newly_failing`) and once when
    it recovers (`recovered`) — not every run in between.

All of this is deterministic and local; no state leaves the machine.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from oneport_apiwatch.config import Config
from oneport_apiwatch.history import History
from oneport_apiwatch.result import Report


def _is_muted(check, now: datetime) -> bool:
    if check.muted:
        return True
    if not check.mute_until:
        return False
    raw = check.mute_until.strip()
    try:
        if len(raw) <= 10:  # date-only
            return date.today() <= date.fromisoformat(raw)
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return now <= dt
    except ValueError:
        return False  # malformed mute date: fail safe = NOT muted (still alerts)


def _trailing_failures(history: History | None, name: str) -> int:
    """Consecutive failed runs at the END of history (before this run)."""
    if history is None:
        return 0
    runs = history.recent(name, limit=50)
    count = 0
    for run in reversed(runs):
        if run.get("ok", True):
            break
        count += 1
    return count


def apply_gate(report: Report, config: Config, history: History | None = None) -> None:
    """Annotate each CheckResult with flap/mute/transition state, in place.

    Must run BEFORE `history.record(report)` so the trailing-failure count
    reflects prior runs, not this one twice.
    """
    now = datetime.now(timezone.utc)
    by_name = {c.name: c for c in config.checks}

    for result in report.checks:
        check = by_name.get(result.name)
        threshold = max(1, getattr(check, "failure_threshold", 1)) if check else 1
        result.threshold = threshold
        result.muted = _is_muted(check, now) if check else False
        result.mute_reason = (check.mute_reason if check and result.muted else "")

        prior = _trailing_failures(history, result.name)
        prior_tripped = prior >= threshold

        if result.ok:
            result.consecutive_failures = 0
            result.tripped = False
            # It was down long enough to have alerted, and now it's back.
            result.recovered = prior_tripped and not result.muted
        else:
            result.consecutive_failures = prior + 1
            result.tripped = (result.consecutive_failures >= threshold) and not result.muted
            result.newly_failing = result.tripped and not prior_tripped

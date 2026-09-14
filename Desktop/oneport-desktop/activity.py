"""OnePort Desktop — per-user local activity log (SQLite).

Every meaningful thing the user does — a scan, a tool run, a chat, an auto-fix,
a report generation — is written to a small SQLite DB in %APPDATA%\\OnePort\\
activity\\<safe-email>.db so the app can:

  • render a Claude-Code-style activity heatmap (buckets: light grey < 9,
    bright white >= 9)
  • show real per-user history that survives across sessions and machines with
    the same account

DESIGN CHOICES that keep this honest:
  • the DB lives PER USER (keyed by their signed-in email). Signed-out users
    get an "anonymous" DB so activity still flows during the login-out state,
    and it's not merged into a real account's DB.
  • writes never block a request path (failure = silent log, never surface an
    error to the user for something as trivial as recording activity).
  • the heatmap counts are REAL counts from the DB — an empty day is 0, not a
    fabricated "quiet day" placeholder.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "OnePort" / "activity"
DATA_DIR.mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()
_CONNS: dict[str, sqlite3.Connection] = {}
_CURRENT_EMAIL: str = "anonymous"

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_email(email: str | None) -> str:
    if not email:
        return "anonymous"
    return _SAFE.sub("_", email.strip().lower()) or "anonymous"


def _db_for(email: str | None) -> sqlite3.Connection:
    key = _safe_email(email)
    with _LOCK:
        conn = _CONNS.get(key)
        if conn is not None:
            return conn
        conn = sqlite3.connect(str(DATA_DIR / f"{key}.db"),
                               check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,          -- ISO-8601 UTC
            day TEXT NOT NULL,         -- YYYY-MM-DD (local time, so heatmap
                                       --   buckets match the user's calendar)
            kind TEXT NOT NULL,        -- scan|tool|chat|fix|report|debt|other
            detail TEXT                -- freeform (tool id, gate id, verdict…)
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_events_day ON events(day)")
        _CONNS[key] = conn
        return conn


def set_current_user(email: str | None) -> None:
    """Called on login/logout so record() picks the right DB automatically."""
    global _CURRENT_EMAIL
    _CURRENT_EMAIL = _safe_email(email)


def record(kind: str, detail: str = "", email: str | None = None) -> None:
    """Log one event. Never raises — activity logging must not break real work."""
    try:
        now = _dt.datetime.now()
        conn = _db_for(email if email is not None else _CURRENT_EMAIL)
        conn.execute("INSERT INTO events(ts,day,kind,detail) VALUES(?,?,?,?)",
                     (now.isoformat(timespec="seconds"),
                      now.strftime("%Y-%m-%d"), kind, (detail or "")[:400]))
    except Exception:
        pass


def _bucket(n: int) -> int:
    """Level for the heatmap. Per user's spec: < 9 = grey, >= 9 = full white.
    Middle levels give a smoother gradient without breaking that top rule."""
    if n <= 0:
        return 0
    if n < 3:
        return 1
    if n < 6:
        return 2
    if n < 9:
        return 3
    return 4  # >= 9 -> full bright white


def heatmap(days: int = 105, email: str | None = None) -> dict:
    """Return {days:[{day,count,level}], total, active_days, best_day, best_count}.
    `days` is inclusive of today. 105 = 15 weeks — the Claude-Code cadence."""
    try:
        conn = _db_for(email if email is not None else _CURRENT_EMAIL)
        today = _dt.date.today()
        start = today - _dt.timedelta(days=days - 1)
        rows = conn.execute(
            "SELECT day, COUNT(*) FROM events WHERE day >= ? GROUP BY day",
            (start.isoformat(),)
        ).fetchall()
        counts = {d: c for d, c in rows}
        out = []
        best_day, best_count = None, 0
        total = 0
        for i in range(days):
            d = start + _dt.timedelta(days=i)
            key = d.isoformat()
            n = counts.get(key, 0)
            total += n
            if n > best_count:
                best_count, best_day = n, key
            out.append({"day": key, "count": n, "level": _bucket(n)})
        active_days = sum(1 for x in out if x["count"] > 0)
        return {"days": out, "total": total, "active_days": active_days,
                "best_day": best_day, "best_count": best_count,
                "window": days, "thresholds": {"light": 1, "bright": 9}}
    except Exception as exc:
        return {"days": [], "total": 0, "active_days": 0, "best_day": None,
                "best_count": 0, "window": days, "error": str(exc)}


def build(proj: dict | None = None, days: int = 119) -> dict:
    """Shape the UI's activity heatmap expects.

    days: [{date, count, level(0-3)}], total, active_days, since_days, source.
    Thresholds match the user's spec: <9 events = grey shades (levels 1-2),
    >=9 events = full white (level 3). Empty days = level 0."""
    def lvl(n: int) -> int:
        if n <= 0:
            return 0
        if n < 4:
            return 1     # sparse
        if n < 9:
            return 2     # medium grey  (< 9)
        return 3         # full white   (>= 9)

    try:
        conn = _db_for(_CURRENT_EMAIL)
        today = _dt.date.today()
        start = today - _dt.timedelta(days=days - 1)
        rows = conn.execute(
            "SELECT day, COUNT(*) FROM events WHERE day >= ? GROUP BY day",
            (start.isoformat(),)
        ).fetchall()
        counts = {d: c for d, c in rows}
        out, total = [], 0
        for i in range(days):
            d = start + _dt.timedelta(days=i)
            n = counts.get(d.isoformat(), 0)
            total += n
            out.append({"date": d.isoformat(), "count": n, "level": lvl(n)})
        return {"days": out, "total": total,
                "active_days": sum(1 for x in out if x["count"] > 0),
                "since_days": days, "source": "actions"}
    except Exception:
        return {"days": [], "total": 0, "active_days": 0,
                "since_days": days, "source": "actions"}


def recent(limit: int = 50, email: str | None = None) -> list[dict]:
    try:
        conn = _db_for(email if email is not None else _CURRENT_EMAIL)
        rows = conn.execute(
            "SELECT ts, kind, detail FROM events ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"ts": r[0], "kind": r[1], "detail": r[2]} for r in rows]
    except Exception:
        return []

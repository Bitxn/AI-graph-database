"""
Data sources for evidence.

Two, both local:
  * the Oneport usage ledger (~/.oneport/usage.db) — written automatically by every
    `op ship` run, so it's the zero-setup source of gate-run history;
  * an optional evidence store (~/.oneport/evidence.db) that `ingest` fills from full
    `op ship --format json` reports, adding finding-level detail.

Both expose the same `GateRun` records so the report layer is source-agnostic.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from oneport_evidence.result import GateRun


def default_ledger_path() -> Path:
    return Path(os.path.expanduser("~")) / ".oneport" / "usage.db"


def default_evidence_db() -> Path:
    return Path(os.path.expanduser("~")) / ".oneport" / "evidence.db"


def repo_fingerprint(path: str | Path) -> str:
    raw = str(Path(path).resolve()).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# ── the usage ledger (read-only) ─────────────────────────────────────────────

def read_ledger_runs(since_ts: int = 0, path: Path | None = None) -> list[GateRun]:
    """Gate runs from the Oneport usage ledger since `since_ts`. [] if absent."""
    p = path or default_ledger_path()
    if not p.exists():
        return []
    try:
        conn = sqlite3.connect(str(p))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ts, tool, status, repo_hash FROM usage WHERE ts >= ? ORDER BY ts",
            (since_ts,)).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    return [GateRun(ts=r["ts"], gate=r["tool"], status=r["status"] or "",
                    repo_hash=r["repo_hash"] or "") for r in rows]


# ── the ingest store (read/write) ────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ship_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    repo_hash TEXT DEFAULT '',
    blocked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gate_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    gate TEXT NOT NULL,
    status TEXT DEFAULT '',
    findings INTEGER DEFAULT 0,
    repo_hash TEXT DEFAULT ''
);
"""


class EvidenceStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_evidence_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def ingest_report(self, report: dict, repo: str | Path = "", ts: int | None = None) -> int:
        """Store one `op ship --format json` report as a ship event + gate results."""
        ts = int(ts if ts is not None else time.time())
        repo_hash = repo_fingerprint(repo) if repo else str(report.get("repo_hash", ""))
        blocked = 1 if report.get("blocked") else 0
        results = report.get("results", []) or []
        with self._conn() as c:
            cur = c.execute("INSERT INTO ship_event (ts, repo_hash, blocked) VALUES (?, ?, ?)",
                            (ts, repo_hash, blocked))
            event_id = cur.lastrowid
            for r in results:
                counts = r.get("counts", {}) or {}
                findings = sum(int(v) for v in counts.values()) if counts else len(r.get("findings", []))
                c.execute(
                    "INSERT INTO gate_result (event_id, ts, gate, status, findings, repo_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (event_id, ts, r.get("tool", ""), r.get("status", ""), findings, repo_hash))
        return event_id

    def read_runs(self, since_ts: int = 0) -> list[GateRun]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, gate, status, findings, repo_hash FROM gate_result "
                "WHERE ts >= ? ORDER BY ts", (since_ts,)).fetchall()
        return [GateRun(ts=r["ts"], gate=r["gate"], status=r["status"] or "",
                        repo_hash=r["repo_hash"] or "", findings=r["findings"] or 0) for r in rows]

    def event_count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM ship_event").fetchone()[0]


def parse_report_json(text: str) -> dict:
    """Parse an op ship JSON report; tolerate a leading log line."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise

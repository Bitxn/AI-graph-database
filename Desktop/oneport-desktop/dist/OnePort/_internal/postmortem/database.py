import sqlite3
import json
import os
import re
from datetime import datetime
from pathlib import Path

# Parsing of the model's text layout lives in one place (schema.py) so the DB,
# the exporter and the JSON output can't drift apart.
from .schema import (
    extract_action_items as _schema_action_items,
    extract_field as _extract_field,
    extract_root_cause as _extract_root_cause,
)


# Store DB in user's home dir so it persists across projects
DB_PATH = os.path.join(Path.home(), ".oneport", "postmortems.db")


def _get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS postmortems (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT    NOT NULL,
            title       TEXT,
            date        TEXT,
            severity    TEXT,
            duration    TEXT,
            impact      TEXT,
            root_cause  TEXT,
            summary     TEXT,
            raw_text    TEXT    NOT NULL,
            source_file TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS action_items (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            postmortem_id  INTEGER NOT NULL,
            priority       TEXT,
            description    TEXT,
            owner          TEXT,
            due_date       TEXT,
            completed      INTEGER DEFAULT 0,
            FOREIGN KEY (postmortem_id) REFERENCES postmortems(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            email        TEXT    NOT NULL UNIQUE,
            display_name TEXT    NOT NULL,
            hashed_pw    TEXT    NOT NULL,
            role         TEXT    NOT NULL DEFAULT 'member',
            created_at   TEXT    NOT NULL,
            last_login   TEXT
        )
    """)
    
    conn.commit()
    conn.close()


def _extract_action_items(raw_text: str) -> list[dict]:
    """Action items as plain dicts (DB/web-app shape), parsed via schema.py."""
    return [
        {"priority": a.priority, "description": a.description,
         "owner": a.owner, "due_date": a.due_date}
        for a in _schema_action_items(raw_text)
    ]


def save_postmortem(raw_text: str, source_file: str = None) -> int:
    """
    Parses and saves a post-mortem to the DB. Returns the new record ID.
    """
    init_db()
    conn = _get_connection()

    title      = _extract_field(raw_text, "TITLE")
    date       = _extract_field(raw_text, "DATE")
    severity   = _extract_field(raw_text, "SEVERITY")
    duration   = _extract_field(raw_text, "DURATION")
    impact     = _extract_field(raw_text, "IMPACT")
    root_cause = _extract_root_cause(raw_text)

    # Extract summary paragraph
    summary_match = re.search(
        r"SUMMARY:\s*\n(.*?)(?=\n[A-Z ]+:|$)", raw_text, re.DOTALL
    )
    summary = summary_match.group(1).strip() if summary_match else ""

    cursor = conn.execute("""
        INSERT INTO postmortems
            (created_at, title, date, severity, duration, impact, root_cause, summary, raw_text, source_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        title, date, severity, duration, impact,
        root_cause, summary, raw_text, source_file
    ))
    postmortem_id = cursor.lastrowid

    # Save action items
    for item in _extract_action_items(raw_text):
        conn.execute("""
            INSERT INTO action_items (postmortem_id, priority, description, owner, due_date)
            VALUES (?, ?, ?, ?, ?)
        """, (
            postmortem_id,
            item["priority"], item["description"],
            item["owner"], item["due_date"]
        ))

    conn.commit()
    conn.close()
    return postmortem_id


def find_similar_incidents(raw_text: str, current_id: int = None) -> list[dict]:
    """
    Compares the root cause and keywords of a new post-mortem against all
    historical ones. Returns a list of similar past incidents.
    """
    init_db()
    conn = _get_connection()

    root_cause = _extract_root_cause(raw_text).lower()

    # Extract meaningful keywords from root cause (skip stop words)
    STOP_WORDS = {
        "the", "a", "an", "was", "is", "in", "on", "to", "of", "and",
        "for", "not", "no", "with", "that", "this", "it", "be", "by",
        "or", "at", "from", "never", "added", "after"
    }
    keywords = [
        w for w in re.findall(r"\b\w{4,}\b", root_cause)
        if w not in STOP_WORDS
    ]

    if not keywords:
        conn.close()
        return []

    # Fetch all past post-mortems (excluding current)
    query = "SELECT * FROM postmortems"
    params = []
    if current_id:
        query += " WHERE id != ?"
        params.append(current_id)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    matches = []
    for row in rows:
        past_root = (row["root_cause"] or "").lower()
        past_summary = (row["summary"] or "").lower()
        combined = past_root + " " + past_summary

        # Score by keyword overlap
        hit_count = sum(1 for kw in keywords if kw in combined)
        score = hit_count / len(keywords) if keywords else 0

        if score >= 0.3:  # 30% keyword overlap = similar enough to flag
            matches.append({
                "id":         row["id"],
                "title":      row["title"] or "Untitled",
                "date":       row["date"] or row["created_at"][:10],
                "severity":   row["severity"] or "?",
                "root_cause": row["root_cause"] or "",
                "score":      round(score * 100),
            })

    # Sort by similarity score descending
    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches[:5]  # top 5 matches


def list_postmortems(limit: int = 20) -> list[dict]:
    """Returns the most recent post-mortems from the DB."""
    init_db()
    conn = _get_connection()
    rows = conn.execute(
        "SELECT id, created_at, title, severity, root_cause FROM postmortems ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    """Returns aggregate stats across all stored post-mortems."""
    init_db()
    conn = _get_connection()

    total = conn.execute("SELECT COUNT(*) FROM postmortems").fetchone()[0]
    by_severity = conn.execute("""
        SELECT severity, COUNT(*) as count
        FROM postmortems
        GROUP BY severity
        ORDER BY count DESC
    """).fetchall()
    top_root_causes = conn.execute("""
        SELECT root_cause, COUNT(*) as count
        FROM postmortems
        WHERE root_cause != ''
        GROUP BY root_cause
        ORDER BY count DESC
        LIMIT 5
    """).fetchall()
    open_actions = conn.execute(
        "SELECT COUNT(*) FROM action_items WHERE completed = 0"
    ).fetchone()[0]

    conn.close()
    return {
        "total": total,
        "by_severity": [dict(r) for r in by_severity],
        "top_root_causes": [dict(r) for r in top_root_causes],
        "open_actions": open_actions,
    }

def create_user(email: str, display_name: str, hashed_pw: str, role: str = "member") -> int:
    init_db()
    conn = _get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO users (email, display_name, hashed_pw, role, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (email.lower().strip(), display_name, hashed_pw, role, datetime.now().isoformat()))
        user_id = cursor.lastrowid
        conn.commit()
        return user_id
    except Exception:
        raise ValueError(f"Email already registered: {email}")
    finally:
        conn.close()


def get_user_by_email(email: str) -> dict | None:
    init_db()
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_last_login(user_id: int):
    init_db()
    conn = _get_connection()
    conn.execute(
        "UPDATE users SET last_login = ? WHERE id = ?",
        (datetime.now().isoformat(), user_id)
    )
    conn.commit()
    conn.close()


def list_users() -> list[dict]:
    init_db()
    conn = _get_connection()
    rows = conn.execute(
        "SELECT id, email, display_name, role, created_at, last_login FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
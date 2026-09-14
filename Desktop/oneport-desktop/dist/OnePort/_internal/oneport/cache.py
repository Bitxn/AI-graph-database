"""
SQLite-backed review cache.

Keys are SHA-256 hashes of (diff_text + model_name) so identical diffs
reviewed with the same model return instantly without an API call.

The cache file lives at:
  Linux/macOS:  ~/.cache/oneport/reviews.db
  Windows:      %LOCALAPPDATA%/oneport/reviews.db
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import platformdirs

from oneport.exceptions import CacheError


CACHE_DIR = Path(platformdirs.user_cache_dir("oneport"))
CACHE_DB = CACHE_DIR / "reviews.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS reviews (
    key         TEXT PRIMARY KEY,
    response    TEXT NOT NULL,
    created_at  INTEGER NOT NULL
)
"""


class ReviewCache:
    def __init__(
        self,
        enabled: bool = True,
        ttl: int = 86_400,
        db_path: Path | None = None,
    ) -> None:
        self.enabled = enabled
        self.ttl = ttl
        self._db_path = db_path or CACHE_DB
        if self.enabled:
            self._init_db()

    # ── Public API ─────────────────────────────────────────────────────────────

    @staticmethod
    def make_key(diff: str, model: str, prompt_context: str = "") -> str:
        """SHA-256 hash of diff + model + prompt context — the cache lookup key.

        `prompt_context` covers anything else that changes what the model would
        say about the same diff (team guidelines, rule config). Without it, a
        newly added guideline would be silently ignored for any diff that's
        already in the cache.
        """
        payload = f"{model}:{prompt_context}:{diff}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def get(self, key: str) -> str | None:
        """Return cached response text or None if missing/expired."""
        if not self.enabled:
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT response, created_at FROM reviews WHERE key = ?", (key,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise CacheError(f"Cache read failed: {exc}") from exc

        if row is None:
            return None

        response, created_at = row
        if time.time() - created_at > self.ttl:
            self._delete(key)
            return None

        return response

    def set(self, key: str, response: str) -> None:
        """Write a response to the cache."""
        if not self.enabled:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO reviews (key, response, created_at) VALUES (?, ?, ?)",
                    (key, response, int(time.time())),
                )
        except sqlite3.Error as exc:
            raise CacheError(f"Cache write failed: {exc}") from exc

    def clear(self) -> None:
        """Delete all cached entries."""
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM reviews")
        except sqlite3.Error as exc:
            raise CacheError(f"Cache clear failed: {exc}") from exc

    def stats(self) -> dict:
        """Return entry count and DB size in KB."""
        try:
            with self._connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
            size_kb = self._db_path.stat().st_size / 1024 if self._db_path.exists() else 0.0
            return {"entries": count, "size_kb": size_kb}
        except sqlite3.Error as exc:
            raise CacheError(f"Cache stats failed: {exc}") from exc

    # ── Private ────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.execute(CREATE_TABLE_SQL)
        except sqlite3.Error as exc:
            # Non-fatal: if we can't create the DB, reviews still work — just no caching.
            self.enabled = False
            raise CacheError(f"Cache init failed — caching disabled: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _delete(self, key: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM reviews WHERE key = ?", (key,))
        except sqlite3.Error:
            pass  # Best-effort expiry cleanup

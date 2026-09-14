"""
Tiny file-backed JSON cache for OSV / registry responses.

Keyed by ecosystem+package+version; entries expire after the configured TTL
(default 24h) so newly published advisories surface within a day. Never caches
LLM triage — evidence changes with the codebase.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

CACHE_DIR = Path.home() / ".oneport-depcheck" / "cache"


class ScanCache:
    def __init__(self, ttl: int = 86_400, cache_dir: Path | None = None) -> None:
        self.ttl = ttl
        self.dir = Path(cache_dir) if cache_dir else CACHE_DIR

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.dir / f"{digest}.json"

    def get(self, key: str):
        path = self._path(key)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - entry.get("ts", 0) > self.ttl:
            return None
        return entry.get("value")

    def set(self, key: str, value) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(
                json.dumps({"ts": time.time(), "value": value}), encoding="utf-8"
            )
        except OSError:
            pass  # cache is best-effort; a full disk must not break a scan

    def clear(self) -> int:
        if not self.dir.exists():
            return 0
        n = 0
        for f in self.dir.glob("*.json"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
        return n

    def stats(self) -> dict:
        if not self.dir.exists():
            return {"entries": 0, "size_kb": 0.0}
        files = list(self.dir.glob("*.json"))
        size = sum(f.stat().st_size for f in files)
        return {"entries": len(files), "size_kb": round(size / 1024, 1)}


class NullCache(ScanCache):
    """--no-cache: reads miss, writes vanish."""

    def __init__(self) -> None:
        super().__init__(ttl=0)

    def get(self, key: str):
        return None

    def set(self, key: str, value) -> None:
        return None

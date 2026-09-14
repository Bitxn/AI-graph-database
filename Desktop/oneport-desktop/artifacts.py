"""
artifacts.py — per-project generated artifacts (reports today; fix-bundles, docs
later). Each artifact's content is a file on disk; a small json holds the index.
Stored under %APPDATA%/OnePort/artifacts/{pid}/.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from projects import DATA_DIR

ART_DIR = DATA_DIR / "artifacts"


def _index_path(pid: str) -> Path:
    return ART_DIR / pid / "index.json"


def _content_path(pid: str, aid: str, ext: str) -> Path:
    return ART_DIR / pid / f"{aid}.{ext}"


def list_artifacts(pid: str) -> list[dict]:
    p = _index_path(pid)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
    return []


def _save_index(pid: str, items: list[dict]) -> None:
    p = _index_path(pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, indent=2), encoding="utf-8")


def add_artifact(pid: str, kind: str, title: str, content: str,
                 ext: str = "html") -> dict:
    aid = uuid.uuid4().hex[:12]
    cp = _content_path(pid, aid, ext)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(content, encoding="utf-8")
    meta = {"id": aid, "kind": kind, "title": title, "ext": ext,
            "created": datetime.now(timezone.utc).isoformat(),
            "bytes": len(content.encode("utf-8"))}
    items = list_artifacts(pid)
    items.insert(0, meta)
    _save_index(pid, items[:100])
    return meta


def get_content(pid: str, aid: str) -> tuple[str, str] | None:
    meta = next((m for m in list_artifacts(pid) if m["id"] == aid), None)
    if not meta:
        return None
    cp = _content_path(pid, aid, meta.get("ext", "html"))
    if not cp.exists():
        return None
    return cp.read_text(encoding="utf-8"), meta.get("ext", "html")


def artifact_file(pid: str, aid: str) -> Path | None:
    meta = next((m for m in list_artifacts(pid) if m["id"] == aid), None)
    if not meta:
        return None
    cp = _content_path(pid, aid, meta.get("ext", "html"))
    return cp if cp.exists() else None


def delete_artifact(pid: str, aid: str) -> bool:
    items = list_artifacts(pid)
    keep = [m for m in items if m["id"] != aid]
    if len(keep) == len(items):
        return False
    meta = next((m for m in items if m["id"] == aid), None)
    if meta:
        cp = _content_path(pid, aid, meta.get("ext", "html"))
        try:
            cp.unlink()
        except OSError:
            pass
    _save_index(pid, keep)
    return True

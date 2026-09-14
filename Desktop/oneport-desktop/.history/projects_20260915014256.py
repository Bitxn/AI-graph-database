"""
Projects + live watch — the core of OnePort Studio.

A "project" is an attached repo the user works in (their "chat = a repo in
progress"). It persists to disk with a full history of scans and the tokens each
drew. A Watcher thread polls the folder; when Claude Code / Codex / anyone edits
a file, it fires a callback so the gates re-run automatically.

Pure stdlib (json + threading + os.walk) so it bundles into the single-file exe
with zero extra dependencies.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "OnePort"
STORE = DATA_DIR / "projects.json"

_lock = threading.RLock()

# Directories a watcher never descends into — noise that isn't the user's code.
IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", "env", ".env",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "site-packages", ".idea", ".vscode", ".next", ".turbo", "target",
    "coverage", ".gradle", ".tox", "__pycache__",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── store ────────────────────────────────────────────────────────────────────
def _load() -> dict:
    with _lock:
        if STORE.exists():
            try:
                return json.loads(STORE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"projects": []}
        return {"projects": []}


def _save(data: dict) -> None:
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_projects() -> list[dict]:
    return _load().get("projects", [])


def get_project(pid: str) -> dict | None:
    return next((p for p in _load().get("projects", []) if p["id"] == pid), None)


def create_project(path: str, base: str = "main") -> dict:
    path = str(Path(path))
    data = _load()
    # Same folder attached twice → return the existing project, don't duplicate.
    for p in data["projects"]:
        if p["path"] == path:
            return p
    proj = {
        "id": uuid.uuid4().hex[:12],
        "name": Path(path).name or path,
        "path": path,
        "base": base,
        "created_at": _now(),
        "tokens_used": 0,
        "watching": False,
        "last_verdict": None,
        "history": [],
    }
    data["projects"].insert(0, proj)
    _save(data)
    return proj


_REPOS_DIR = Path.home() / "OnePort" / "repos"
_NO_WINDOW = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)


def _repo_name(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    # keep it filesystem-safe
    return "".join(c for c in name if c.isalnum() or c in "._-") or "repo"


def clone_repo(url: str) -> dict:
    """Clone a git URL (GitHub/GitLab/Bitbucket/any) into ~/OnePort/repos/<name>
    so every git-based tool works — no more ZIP-vs-clone confusion. Shallow-ish
    (depth 50: fast, but enough history for the diff-based checks). Public repos
    work out of the box; a private repo fails with git's own auth message, which
    we surface cleanly."""
    import subprocess
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "Paste a repository URL first."}
    if not (url.startswith("http://") or url.startswith("https://")
            or url.startswith("git@") or url.startswith("ssh://")):
        return {"ok": False, "error": "That doesn't look like a git URL "
                "(e.g. https://github.com/user/repo)."}
    dest = _REPOS_DIR / _repo_name(url)
    if (dest / ".git").exists():
        return {"ok": True, "path": str(dest), "existed": True}
    try:
        _REPOS_DIR.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["git", "clone", "--depth", "50", url, str(dest)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600, creationflags=_NO_WINDOW)
    except FileNotFoundError:
        return {"ok": False, "error": "Git isn't installed. Install Git for Windows "
                "(git-scm.com) and restart OnePort."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Clone timed out (very large repo). Try again "
                "or clone it manually."}
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        msg = tail[-1][:220] if tail else "git clone failed"
        if "Authentication" in (proc.stderr or "") or "could not read" in (proc.stderr or "").lower():
            msg = "That looks like a private repo — OnePort can only clone public "\
                  "URLs for now. Clone it locally and attach the folder instead."
        return {"ok": False, "error": msg}
    return {"ok": True, "path": str(dest)}


def delete_project(pid: str) -> bool:
    data = _load()
    before = len(data["projects"])
    data["projects"] = [p for p in data["projects"] if p["id"] != pid]
    _save(data)
    return len(data["projects"]) < before


def _mutate(pid: str, fn) -> dict | None:
    with _lock:
        data = _load()
        for p in data["projects"]:
            if p["id"] == pid:
                fn(p)
                _save(data)
                return p
    return None


def record_change(pid: str, files: list) -> dict | None:
    """Log a detected file change INSTANTLY (before any scan) so the UI can show
    'what just changed' live, without waiting for gates to finish."""
    ts = _now()

    def apply(p):
        rc = p.get("recent_changes") or []
        rc.insert(0, {"files": list(files)[:12], "ts": ts, "scanned": False})
        p["recent_changes"] = rc[:40]
        p["last_change_at"] = ts
        return p

    return _mutate(pid, apply)


def mark_change_scanned(pid: str, verdict: str) -> dict | None:
    """Stamp the most recent change with the scan verdict once it completes."""
    def apply(p):
        rc = p.get("recent_changes") or []
        if rc:
            rc[0]["scanned"] = True
            rc[0]["verdict"] = verdict
        return p

    return _mutate(pid, apply)


def set_change_intel(pid: str, intel: dict) -> dict | None:
    """Store the latest advanced change-intelligence record (what changed, its
    repercussions, and whether it introduced new errors) for live display."""
    def apply(p):
        p["last_change_intel"] = intel
        rc = p.get("recent_changes") or []
        if rc:
            rc[0]["risk"] = intel.get("risk_level")     # colour the recent-change row
        return p

    return _mutate(pid, apply)


def add_history(pid: str, entry: dict, tokens: int = 0) -> dict | None:
    """Append a scan record and roll up the verdict + token total."""
    entry = {"ts": _now(), **entry}

    def apply(p):
        p["history"].insert(0, entry)
        p["history"] = p["history"][:200]          # keep it bounded
        p["tokens_used"] = int(p.get("tokens_used", 0)) + max(0, tokens)
        p["last_verdict"] = entry.get("verdict")

    return _mutate(pid, apply)


def add_alert(pid: str, alert: dict) -> dict | None:
    """Push a live alert (a gate blocker or a verification deviation) onto the
    project so the UI can notify the user. Newest first, bounded."""
    def apply(p):
        al = p.get("alerts") or []
        al.insert(0, {"ts": _now(), **alert})
        p["alerts"] = al[:30]
        return p
    return _mutate(pid, apply)


def clear_alerts(pid: str) -> dict | None:
    return _mutate(pid, lambda p: p.__setitem__("alerts", []))


def set_watching(pid: str, on: bool) -> dict | None:
    return _mutate(pid, lambda p: p.__setitem__("watching", bool(on)))


def add_tokens(pid: str, n: int) -> dict | None:
    return _mutate(pid, lambda p: p.__setitem__(
        "tokens_used", int(p.get("tokens_used", 0)) + max(0, int(n))))


# ── watcher ──────────────────────────────────────────────────────────────────
def _signature(root: str) -> dict:
    sig = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in IGNORE_DIRS and not d.startswith(".")]
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                sig[fp] = os.path.getmtime(fp)
            except OSError:
                pass
    return sig


class Watcher(threading.Thread):
    """Polls a folder's file mtimes; fires on_change(changed_paths) when the tree
    changes, after a short quiet period so a burst of edits triggers one run."""

    def __init__(self, pid: str, root: str, on_change, interval: float = 2.5,
                 quiet: float = 1.2):
        super().__init__(daemon=True)
        self.pid, self.root, self.on_change = pid, root, on_change
        self.interval, self.quiet = interval, quiet
        self._stop = threading.Event()
        self._sig: dict | None = None

    def run(self) -> None:
        try:
            self._sig = _signature(self.root)
        except Exception:
            self._sig = {}
        while not self._stop.wait(self.interval):
            try:
                new = _signature(self.root)
            except Exception:
                continue
            changed = _diff(self._sig or {}, new)
            if not changed:
                continue
            # Debounce: keep sampling until the tree goes quiet, so a save-storm
            # (formatter, multi-file AI edit) collapses into a single run.
            while True:
                time.sleep(self.quiet)
                try:
                    newer = _signature(self.root)
                except Exception:
                    newer = new
                more = _diff(new, newer)
                new = newer
                if not more:
                    break
            self._sig = new
            try:
                self.on_change(_diff_names(changed))
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()


def _diff(old: dict, new: dict) -> list[str]:
    changed = [p for p, m in new.items() if old.get(p) != m]
    changed += [p for p in old if p not in new]
    return changed


def _diff_names(paths: list[str]) -> list[str]:
    return [os.path.basename(p) for p in paths][:20]


# Live watcher registry (not persisted — rebuilt when the app starts watching).
_watchers: dict[str, Watcher] = {}


def start_watch(pid: str, root: str, on_change) -> None:
    stop_watch(pid)
    w = Watcher(pid, root, on_change)
    _watchers[pid] = w
    w.start()
    set_watching(pid, True)


def stop_watch(pid: str) -> None:
    w = _watchers.pop(pid, None)
    if w:
        w.stop()
    set_watching(pid, False)


def is_watching(pid: str) -> bool:
    return pid in _watchers

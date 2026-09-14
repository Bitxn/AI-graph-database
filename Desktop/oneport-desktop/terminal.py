"""
terminal.py — an in-app terminal wired to a real background shell.

Runs commands through the OS shell (cmd.exe / bash) in a tracked working
directory, with NO flashing console window, and streams their output into a ring
buffer the UI polls. `cd` is handled specially so the directory persists between
commands, the way a real terminal session does. The gate scans also echo their
commands here, so a scan reads like a live terminal instead of popping windows.
"""
from __future__ import annotations

import itertools
import os
import subprocess
import threading
from collections import deque

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_IS_WIN = os.name == "nt"


class Terminal:
    def __init__(self, cwd: str | None = None):
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self._buf: deque = deque(maxlen=4000)
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self._running = 0
        self.emit(f"OnePort terminal — {self.cwd}")

    # ── output ring buffer ────────────────────────────────────────────────
    def emit(self, text: str, kind: str = "out") -> None:
        with self._lock:
            for line in str(text).split("\n"):
                self._buf.append({"seq": next(self._seq), "text": line, "kind": kind})

    def read_since(self, since: int) -> dict:
        with self._lock:
            lines = [m for m in self._buf if m["seq"] > since]
            last = self._buf[-1]["seq"] if self._buf else since
        return {"lines": lines, "last": last, "cwd": self.cwd, "busy": self._running > 0}

    # ── shell ─────────────────────────────────────────────────────────────
    def set_cwd(self, path: str) -> None:
        if os.path.isdir(path):
            self.cwd = os.path.abspath(path)
            self.emit(f"[cd] {self.cwd}", "sys")

    def send(self, command: str) -> None:
        cmd = (command or "").strip()
        if not cmd:
            return
        self.emit(f"{self.cwd}> {cmd}", "cmd")

        low = cmd.lower()
        if low in ("cls", "clear"):
            with self._lock:
                self._buf.clear()
            return
        if low == "cd" or low.startswith("cd "):
            self._chdir(cmd[2:].strip().strip('"'))
            return

        threading.Thread(target=self._run, args=(cmd,), daemon=True).start()

    def _chdir(self, target: str) -> None:
        if not target:
            self.emit(self.cwd, "out")
            return
        dest = target if os.path.isabs(target) else os.path.join(self.cwd, target)
        dest = os.path.abspath(dest)
        if os.path.isdir(dest):
            self.cwd = dest
        else:
            self.emit("The system cannot find the path specified.", "err")

    def _run(self, cmd: str) -> None:
        with self._lock:
            self._running += 1
        try:
            shell_cmd = ["cmd", "/c", cmd] if _IS_WIN else ["/bin/sh", "-c", cmd]
            proc = subprocess.Popen(
                shell_cmd, cwd=self.cwd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=_NO_WINDOW,
            )
            for line in proc.stdout:
                self.emit(line.rstrip("\n"), "out")
            proc.wait()
        except Exception as exc:
            self.emit(f"[error: {exc}]", "err")
        finally:
            with self._lock:
                self._running -= 1


# One terminal per app session, lazily created.
_TERM: Terminal | None = None


def get_terminal(cwd: str | None = None) -> Terminal:
    global _TERM
    if _TERM is None:
        _TERM = Terminal(cwd)
    return _TERM

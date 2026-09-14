"""
OnePort Desktop — native app entrypoint.

Starts the local engine (server.py) on a background thread bound to 127.0.0.1
and opens it in a native window via pywebview (WebView2 on Windows).

The whole OnePort tool suite is bundled INSIDE this exe. Invoked as
`OnePort.exe __tool <name> <args…>` it *becomes* that tool (see tools.py); the
gates and the in-app terminal route through this, so nothing needs installing.

  python app.py             open the app window
  python app.py --selftest  headless: boot engine, hit /api/status, exit
  <exe> __tool <name> …     run a bundled OnePort tool
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

# MUST run before anything heavy imports — if we're being called as a bundled
# tool, dispatch and exit without spinning up the server/webview.
import tools
tools.maybe_dispatch()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(port: int) -> ThreadingHTTPServer:
    from server import Handler  # lazy — not needed on the __tool path
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _setup_bundled_tools() -> None:
    """Point the gates + terminal at the tools bundled in THIS exe, so users
    never install anything. Only meaningful in the frozen app."""
    if not getattr(sys, "frozen", False):
        return
    # Gates run tools via `<exe> __tool <name> <args>` (see gates.py runner).
    os.environ["ONEPORT_TOOL_RUNNER"] = f"{sys.executable}|__tool"
    # The terminal reaches them via .bat shims on PATH.
    try:
        bindir = Path(os.environ.get("APPDATA") or Path.home()) / "OnePort" / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        for name in tools.TOOLS:
            (bindir / f"{name}.bat").write_text(
                f'@echo off\r\n"{sys.executable}" __tool {name} %*\r\n',
                encoding="utf-8")
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
    except OSError:
        pass


def _selftest_marker() -> str:
    import tempfile
    return os.path.join(tempfile.gettempdir(), "oneport_selftest.txt")


def selftest() -> int:
    port = _free_port()
    _serve(port)
    result, code = "FAIL: engine did not respond", 1
    for _ in range(50):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=1
            ) as r:
                body = r.read().decode("utf-8")
            result, code = "OK: " + body[:300], 0
            break
        except Exception:
            time.sleep(0.1)
    print("engine", result)
    try:
        with open(_selftest_marker(), "w", encoding="utf-8") as fh:
            fh.write(result)
    except OSError:
        pass
    return code


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(prog="OnePort")
    ap.add_argument("--selftest", action="store_true",
                    help="boot engine, check it responds, exit (no window)")
    args = ap.parse_args()

    _setup_bundled_tools()

    if args.selftest:
        raise SystemExit(selftest())

    import webview  # imported only for the GUI path

    port = _free_port()
    srv = _serve(port)
    webview.create_window(
        "OnePort — Security Cockpit",
        f"http://127.0.0.1:{port}",
        width=1120, height=800, min_size=(900, 640),
    )
    try:
        webview.start()  # blocks until the window is closed
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()

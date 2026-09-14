"""
Dev launcher — run the OnePort engine from source and open it in your browser.

No pywebview needed: the app UI is a local web app, so a normal browser tab is a
complete way to use it. This reads the LIVE ui/index.html + server.py + verify.py,
so every source change shows up on refresh — great for iterating without rebuilding
the exe.

    python serve_dev.py
"""
from __future__ import annotations

import socket
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer

from server import Handler


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("\n  OnePort dev engine running (live source).")
    print(f"  Opening {url}  — Verify button + alert bell are in the top bar.")
    print("  Press Ctrl+C here to stop.\n")
    try:
        webbrowser.open(url)
    except Exception:
        print(f"  Couldn't auto-open a browser — paste this in yourself: {url}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("  stopped.")


if __name__ == "__main__":
    main()

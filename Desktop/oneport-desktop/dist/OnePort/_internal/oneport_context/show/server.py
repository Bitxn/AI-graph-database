# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""Serve the walkthrough on localhost and open the browser."""
from __future__ import annotations

import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from oneport_debug_core.cli.output import console
from oneport_context.show.template import render_html


def serve_walkthrough(index, story: dict, port: int = 7000, open_browser: bool = True) -> None:
    html = render_html(story).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *args):  # silence per-request logging
            pass

    httpd, bound = None, port
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            bound = candidate
            break
        except OSError:
            continue
    if httpd is None:
        console.print(f"[red]Could not bind a port in {port}–{port+19}.[/red]")
        return

    url = f"http://127.0.0.1:{bound}"
    console.print()
    console.print(f"  [bold green]▶ Walkthrough ready[/bold green] — {index.name}")
    console.print(f"  [cyan]{url}[/cyan]   [dim](click ▶ play for narration · ←/→ to navigate · Ctrl+C to stop)[/dim]\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Walkthrough stopped.[/dim]")
    finally:
        httpd.server_close()

# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""Serve the generated PDF on localhost and open it in the browser (inline render)."""
from __future__ import annotations

import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from oneport_debug_core.cli.output import console


def serve_pdf(pdf_path: Path, name: str = "handbook", port: int = 7100, open_browser: bool = True) -> None:
    data = Path(pdf_path).read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", 'inline; filename="handbook.pdf"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
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
        console.print(f"[yellow]Could not bind a port in {port}–{port+19}. PDF saved at {pdf_path}[/yellow]")
        return

    url = f"http://127.0.0.1:{bound}"
    console.print()
    console.print(f"  [bold green]Handbook ready[/bold green] - {name}")
    console.print(f"  [cyan]{url}[/cyan]   [dim](opens in your browser - Ctrl+C to stop - saved at {pdf_path})[/dim]\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")
    finally:
        httpd.server_close()

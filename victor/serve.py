"""Tiny HTTP server that runs beside the loop on always-on hosts.

Free hosts (Render, Hugging Face Spaces, Koyeb) keep a service alive only while it answers HTTP, and an
uptime pinger keeps the pings coming. /health answers the pinger; / serves the dashboard snapshot.
"""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import ROOT

log = logging.getLogger(__name__)
DASHBOARD = ROOT / "docs" / "index.html"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/health"):
            body = b"ok"
            ctype = "text/plain"
        else:
            body = DASHBOARD.read_bytes() if DASHBOARD.exists() else b"<h1>Victor newsroom</h1><p>dashboard not written yet</p>"
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt, *args):  # quiet: pingers hit this every few minutes
        return


def start_http(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    t = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    t.start()
    log.info("http server on :%d (/health, /)", port)
    return server

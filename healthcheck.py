"""HTTP healthcheck server for production monitoring."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _format_uptime(seconds: int) -> str:
    days = seconds // 86_400
    hours = (seconds % 86_400) // 3_600
    minutes = (seconds % 3_600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_elapsed(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3_600:
        return f"{seconds // 60}m ago"
    if seconds < 86_400:
        return f"{seconds // 3_600}h ago"
    return f"{seconds // 86_400}d ago"


def _get_db_size(db_path: str) -> str:
    try:
        size = os.path.getsize(db_path)
    except OSError:
        return "0.0MB"
    return f"{(size / (1024 * 1024)):.1f}MB"


def _get_last_message_age(db_path: str) -> str:
    if not os.path.exists(db_path):
        return "never"
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = db.execute(
                "SELECT created_at FROM conversations ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        finally:
            db.close()
        if not row or not row[0]:
            return "never"
        last_message_dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        elapsed = max(0, int((datetime.now(timezone.utc) - last_message_dt).total_seconds()))
        return _format_elapsed(elapsed)
    except Exception:
        return "unknown"


def start_healthcheck_server(
    db_path: str, host: str = "0.0.0.0", port: int = 8080
) -> ThreadingHTTPServer:
    """Start `/health` endpoint in a daemon thread and return server handle."""
    started_at = time.monotonic()

    class _HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return

            payload = {
                "status": "ok",
                "uptime": _format_uptime(int(time.monotonic() - started_at)),
                "last_message": _get_last_message_age(db_path),
                "db_size": _get_db_size(db_path),
            }
            body = json.dumps(payload).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), _HealthHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="healthcheck-server",
        daemon=True,
    )
    thread.start()
    return server

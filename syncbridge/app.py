from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from importlib.resources import files

from .adapters import send_notion, send_rest
from .mapping import FieldMap
from .config import database_url
from .postgres_store import PostgresStore
from .store import Store


class Runtime:
    def __init__(self):
        dsn = database_url()
        self.storage_backend = "postgres" if dsn else "sqlite"
        self.store = PostgresStore(dsn) if self.storage_backend == "postgres" else Store(os.getenv("SYNCBRIDGE_DB", "data/syncbridge.db"))
        self.mapper = FieldMap.from_file(os.getenv("SYNCBRIDGE_FIELD_MAP"))
        self.api_token = os.environ["SYNCBRIDGE_API_TOKEN"]
        self.webhook_secret = os.environ["SYNCBRIDGE_WEBHOOK_SECRET"]
        self.stop = threading.Event()

    def health(self) -> dict:
        destination = os.getenv("SYNCBRIDGE_DESTINATION", "rest")
        configured = (
            bool(os.getenv("NOTION_DATABASE_ID") and os.getenv("NOTION_TOKEN"))
            if destination == "notion"
            else bool(os.getenv("DESTINATION_URL"))
        )
        try:
            self.store.stats()
            database_ready = True
        except Exception:
            database_ready = False
        return {
            "status": "ok" if database_ready else "degraded",
            "database": self.storage_backend,
            "database_ready": database_ready,
            "destination": destination,
            "destination_configured": configured,
        }

    def deliver(self, payload: dict):
        kind = os.getenv("SYNCBRIDGE_DESTINATION", "rest")
        if kind == "notion":
            send_notion(os.environ["NOTION_DATABASE_ID"], os.environ["NOTION_TOKEN"], payload, os.getenv("NOTION_KEY_PROPERTY"))
        else:
            send_rest(os.environ["DESTINATION_URL"], os.getenv("DESTINATION_TOKEN", ""), payload)

    def worker(self):
        while not self.stop.is_set():
            try:
                event = self.store.claim()
            except Exception:
                # No event was returned; retry acquisition after a bounded wait.
                # Never include DSNs/provider exception messages in stdout.
                print(json.dumps({"event": "queue_claim_failed", "retry_in_seconds": 5}))
                self.stop.wait(5)
                continue
            if not event:
                self.stop.wait(1)
                continue
            try:
                raw = event["payload"]
                self.deliver(self.mapper.apply(json.loads(raw) if isinstance(raw, str) else raw))
            except Exception as exc:
                recorded = self.record_outcome(
                    self.store.fail, event["id"], event["attempts"] + 1, str(exc)
                )
            else:
                # A failed acknowledgement is not a failed delivery. Retry only
                # the database write, never send the accepted payload again here.
                recorded = self.record_outcome(self.store.finish, event["id"])
            if not recorded:
                return

    def record_outcome(self, operation, *args) -> bool:
        while True:
            try:
                operation(*args)
                return True
            except Exception:
                print(json.dumps({"event": "queue_outcome_write_failed", "retry_in_seconds": 5}))
                if self.stop.wait(5):
                    return False


def handler(runtime: Runtime):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SyncBridge/0.1"

        def json(self, status: int, data: dict):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def html(self, body: str):
            encoded = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(encoded)

        def authorized(self):
            return hmac.compare_digest(
                self.headers.get("Authorization", "").encode("utf-8"), f"Bearer {runtime.api_token}".encode("utf-8")
            )

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                return self.html(files("syncbridge").joinpath("dashboard.html").read_text(encoding="utf-8"))
            if path == "/health":
                health = runtime.health()
                return self.json(200 if health.get("status") == "ok" else 503, health)
            if path == "/api/events":
                if not self.authorized():
                    return self.json(401, {"error": "unauthorized"})
                try:
                    data = {"events": runtime.store.list_events(), "stats": runtime.store.stats()}
                except Exception:
                    return self.json(503, {"error": "storage_unavailable"})
                return self.json(200, data)
            if path == "/metrics":
                if not self.authorized():
                    return self.json(401, {"error": "unauthorized"})
                try:
                    stats = runtime.store.stats()
                except Exception:
                    return self.json(503, {"error": "storage_unavailable"})
                lines = [f'syncbridge_events{{status="{k}"}} {v}' for k, v in stats.items()]
                body = ("\n".join(lines) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            return self.json(404, {"error": "not_found"})

        def do_POST(self):
            path = urlparse(self.path).path
            if path.startswith("/api/events/") and path.endswith("/retry"):
                if not self.authorized():
                    return self.json(401, {"error": "unauthorized"})
                try:
                    event_id = int(path.split("/")[3])
                except (ValueError, IndexError):
                    return self.json(400, {"error": "invalid_event_id"})
                try:
                    retried = runtime.store.retry(event_id)
                except Exception:
                    return self.json(503, {"error": "storage_unavailable"})
                if not retried:
                    return self.json(409, {"error": "event_not_retryable"})
                return self.json(202, {"id": event_id, "status": "retry"})
            if not path.startswith("/webhooks/"):
                return self.json(404, {"error": "not_found"})
            source = path.removeprefix("/webhooks/")
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", source):
                return self.json(400, {"error": "invalid_source"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.json(400, {"error": "invalid_content_length"})
            if length <= 0 or length > 1_000_000:
                return self.json(413, {"error": "invalid_size"})
            raw = self.rfile.read(length)
            expected = hmac.new(runtime.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
            signature = self.headers.get("X-SyncBridge-Signature", "")
            if not re.fullmatch(r"[0-9a-f]{64}", signature) or not hmac.compare_digest(signature, expected):
                return self.json(401, {"error": "invalid_signature"})
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError
            except (UnicodeDecodeError, ValueError):
                return self.json(400, {"error": "invalid_json"})
            key = self.headers.get("Idempotency-Key") or hashlib.sha256(raw).hexdigest()
            if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", key):
                return self.json(400, {"error": "invalid_idempotency_key"})
            try:
                event_id, created = runtime.store.ingest(source, key, payload)
            except Exception:
                return self.json(503, {"error": "storage_unavailable"})
            return self.json(202 if created else 200, {"id": event_id, "created": created})

        def log_message(self, fmt, *args):
            print(json.dumps({"ts": int(time.time()), "message": fmt % args}))

    return Handler


def serve(host="0.0.0.0", port=8080):
    runtime = Runtime()
    thread = threading.Thread(target=runtime.worker, daemon=True)
    thread.start()
    try:
        ThreadingHTTPServer((host, port), handler(runtime)).serve_forever()
    finally:
        runtime.stop.set()

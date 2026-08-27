from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .adapters import send_notion, send_rest
from .store import Store


class Runtime:
    def __init__(self):
        self.store = Store(os.getenv("SYNCBRIDGE_DB", "data/syncbridge.db"))
        self.api_token = os.environ["SYNCBRIDGE_API_TOKEN"]
        self.webhook_secret = os.environ["SYNCBRIDGE_WEBHOOK_SECRET"]
        self.stop = threading.Event()

    def deliver(self, payload: dict):
        kind = os.getenv("SYNCBRIDGE_DESTINATION", "rest")
        if kind == "notion":
            send_notion(os.environ["NOTION_DATABASE_ID"], os.environ["NOTION_TOKEN"], payload)
        else:
            send_rest(os.environ["DESTINATION_URL"], os.getenv("DESTINATION_TOKEN", ""), payload)

    def worker(self):
        while not self.stop.is_set():
            event = self.store.claim()
            if not event:
                self.stop.wait(1)
                continue
            try:
                self.deliver(json.loads(event["payload"]))
                self.store.finish(event["id"])
            except Exception as exc:
                self.store.fail(event["id"], event["attempts"] + 1, str(exc))


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

        def authorized(self):
            return hmac.compare_digest(
                self.headers.get("Authorization", ""), f"Bearer {runtime.api_token}"
            )

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                return self.json(200, {"status": "ok", "database": "sqlite"})
            if path == "/metrics":
                if not self.authorized():
                    return self.json(401, {"error": "unauthorized"})
                stats = runtime.store.stats()
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
            if not path.startswith("/webhooks/"):
                return self.json(404, {"error": "not_found"})
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                return self.json(413, {"error": "invalid_size"})
            raw = self.rfile.read(length)
            expected = hmac.new(runtime.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(self.headers.get("X-SyncBridge-Signature", ""), expected):
                return self.json(401, {"error": "invalid_signature"})
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError
            except (json.JSONDecodeError, ValueError):
                return self.json(400, {"error": "invalid_json"})
            source = path.removeprefix("/webhooks/")
            key = self.headers.get("Idempotency-Key") or hashlib.sha256(raw).hexdigest()
            event_id, created = runtime.store.ingest(source, key, payload)
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

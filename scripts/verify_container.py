"""Exercise the built Linux container against a disposable PostgreSQL database.

The local HTTP receiver is a test fixture, not a claim of real CRM acceptance.
Run: DATABASE_URL=... python scripts/verify_container.py IMAGE
"""
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def eventually(check, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.5)
    raise AssertionError("container verification timed out")


def verify(image):
    dsn = os.environ["DATABASE_URL"]
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise ValueError("a disposable PostgreSQL DATABASE_URL is required")
    token, secret, destination_token = (secrets.token_hex(24) for _ in range(3))
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Authorization") != f"Bearer {destination_token}":
                self.send_error(401)
                return
            received.append(json.loads(body))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    name = "syncbridge-smoke-" + secrets.token_hex(6)
    env = os.environ.copy()
    env.update(SYNCBRIDGE_API_TOKEN=token, SYNCBRIDGE_WEBHOOK_SECRET=secret,
               SYNCBRIDGE_DESTINATION="rest", DESTINATION_TOKEN=destination_token,
               DESTINATION_URL=f"http://127.0.0.1:{receiver.server_port}/records")
    base = f"http://127.0.0.1:{port}"

    def request(path, body=None, headers=None):
        req = Request(base + path, data=body, headers=headers or {})
        with urlopen(req, timeout=3) as response:
            return response.status, json.load(response)

    def ready():
        status, health = request("/health")
        return status == 200 and health.get("database") == "postgres" and health.get("database_ready") is True

    def events():
        return request("/api/events", headers={"Authorization": f"Bearer {token}"})[1]["events"]

    try:
        command = ["docker", "run", "--detach", "--name", name,
                   "--network", "host", "--read-only", "--tmpfs", "/tmp"]
        for key in ("DATABASE_URL", "SYNCBRIDGE_API_TOKEN", "SYNCBRIDGE_WEBHOOK_SECRET",
                    "SYNCBRIDGE_DESTINATION", "DESTINATION_TOKEN", "DESTINATION_URL"):
            command.extend(["--env", key])
        command.extend([image, "syncbridge", "serve", "--host", "127.0.0.1", "--port", str(port)])
        subprocess.run(command, env=env, check=True, capture_output=True)
        eventually(ready)
        uid = subprocess.check_output(["docker", "exec", name, "id", "-u"], text=True).strip()
        assert uid != "0", "container must not run as root"
        for private_path in ("/app/.env", "/app/.git", "/app/.venv", "/app/data"):
            subprocess.run(["docker", "exec", name, "test", "!", "-e", private_path],
                           check=True, capture_output=True)
        try:
            request("/api/events")
            raise AssertionError("unauthenticated operator access succeeded")
        except HTTPError as error:
            assert error.code == 401
            error.close()
        body = json.dumps({"external_id": name, "kind": "container-test-fixture"}).encode()
        headers = {"Content-Type": "application/json", "Idempotency-Key": name,
                   "X-SyncBridge-Signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}
        invalid = dict(headers, **{"X-SyncBridge-Signature": "invalid"})
        try:
            request("/webhooks/container-smoke", body, invalid)
            raise AssertionError("invalid webhook signature accepted")
        except HTTPError as error:
            assert error.code == 401
            error.close()
        status, accepted = request("/webhooks/container-smoke", body, headers)
        assert status == 202 and accepted["created"] is True
        status, duplicate = request("/webhooks/container-smoke", body, headers)
        assert status == 200 and duplicate == {"id": accepted["id"], "created": False}

        def delivered():
            return any(row["id"] == accepted["id"] and row["status"] == "done" for row in events())

        eventually(delivered)
        assert received == [json.loads(body)], "expected one authenticated REST delivery"
        subprocess.run(["docker", "restart", name], check=True, capture_output=True)
        eventually(ready)
        assert delivered(), "persisted event missing after container restart"
        status, duplicate = request("/webhooks/container-smoke", body, headers)
        assert status == 200 and duplicate["created"] is False
        assert received == [json.loads(body)]
        print("PASS: non-root/read-only container, PostgreSQL, auth, signed webhook, dedup, REST delivery, restart persistence")
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True)
        receiver.shutdown()
        receiver.server_close()
        thread.join()


if __name__ == "__main__":
    verify(sys.argv[1])

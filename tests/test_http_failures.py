import hashlib
import hmac
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

from syncbridge.app import handler


class HTTPFailureTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.runtime = SimpleNamespace(store=self.store, api_token="test-token", webhook_secret="test-secret")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.runtime))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def signed(self, body):
        return {"X-SyncBridge-Signature": hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()}

    def test_non_ascii_credentials_are_rejected_not_disconnected(self):
        self.assertEqual(self.request("GET", "/api/events", headers={"Authorization": "Bearer é"})[0], 401)
        self.assertEqual(self.request("POST", "/webhooks/crm", b"{}", {"X-SyncBridge-Signature": "é"})[0], 401)
        self.store.ingest.assert_not_called()
        self.store.list_events.assert_not_called()

    def test_valid_signature_with_invalid_encoding_returns_400(self):
        body = b'{"name":"\xff"}'
        self.assertEqual(self.request("POST", "/webhooks/crm", body, self.signed(body)), (400, {"error": "invalid_json"}))
        self.store.ingest.assert_not_called()

    def test_storage_failures_return_safe_503_for_all_operator_routes(self):
        for operation in (self.store.list_events, self.store.stats, self.store.retry):
            operation.side_effect = RuntimeError("postgres://private-secret@db/customer")
        for method, path in (("GET", "/api/events"), ("GET", "/metrics"), ("POST", "/api/events/1/retry")):
            with self.subTest(path=path):
                self.assertEqual(self.request(method, path, headers={"Authorization": "Bearer test-token"}), (503, {"error": "storage_unavailable"}))

    def test_webhook_storage_failure_then_recovery_keeps_idempotency_key(self):
        self.store.ingest.side_effect = [RuntimeError("private-secret"), (7, False)]
        body = b'{"email":"test@example.invalid"}'
        headers = {**self.signed(body), "Idempotency-Key": "stable-retry-key"}
        self.assertEqual(self.request("POST", "/webhooks/crm", body, headers), (503, {"error": "storage_unavailable"}))
        self.assertEqual(self.request("POST", "/webhooks/crm", body, headers), (200, {"id": 7, "created": False}))
        self.assertEqual(self.store.ingest.call_args_list[0], self.store.ingest.call_args_list[1])

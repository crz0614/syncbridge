import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from syncbridge.app import handler
from syncbridge.csv_ingest import import_csv
from syncbridge.mapping import FieldMap
from syncbridge.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(f"{self.tmp.name}/test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_ingest_is_idempotent(self):
        first, created = self.store.ingest("crm", "same", {"name": "real-input"})
        second, duplicate = self.store.ingest("crm", "same", {"name": "changed"})
        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertEqual(first, second)

    def test_claim_and_finish(self):
        event_id, _ = self.store.ingest("crm", "1", {"id": 1})
        event = self.store.claim()
        self.assertEqual(event_id, event["id"])
        self.assertEqual({"id": 1}, json.loads(event["payload"]))
        self.store.finish(event_id)
        self.assertEqual({"done": 1}, self.store.stats())

    def test_failure_retries_then_dead_letters(self):
        event_id, _ = self.store.ingest("crm", "2", {"id": 2})
        self.store.claim()
        self.store.fail(event_id, 5, "destination unavailable")
        self.assertEqual({"dead": 1}, self.store.stats())

    def test_list_and_operator_retry(self):
        event_id, _ = self.store.ingest("crm", "retry-me", {"id": 2})
        self.store.claim()
        self.store.fail(event_id, 5, "destination unavailable")
        events = self.store.list_events()
        self.assertEqual(event_id, events[0]["id"])
        self.assertEqual("dead", events[0]["status"])
        self.assertNotIn("payload", events[0])
        self.assertTrue(self.store.retry(event_id))
        self.assertEqual({"retry": 1}, self.store.stats())

    def test_field_map_rejects_non_string_rules(self):
        path = Path(self.tmp.name) / "map.json"
        path.write_text('{"source": 1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            FieldMap.from_file(str(path))

    def test_csv_import_maps_and_deduplicates(self):
        path = Path(self.tmp.name) / "records.csv"
        path.write_text("customer_id,name\n42,Ada\n", encoding="utf-8")
        mapper = FieldMap({"customer_id": "External ID", "name": "Name"})
        self.assertEqual({"created": 1, "duplicates": 0}, import_csv(self.store, str(path), field_map=mapper))
        self.assertEqual({"created": 0, "duplicates": 1}, import_csv(self.store, str(path), field_map=mapper))


class HealthTests(unittest.TestCase):
    def test_health_reports_the_real_backend_and_destination_state(self):
        runtime = SimpleNamespace(
            api_token="operator-token",
            health=lambda: {
                "status": "ok",
                "database": "postgres",
                "destination": "rest",
                "destination_configured": True,
            },
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(runtime))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/health") as response:
                body = json.load(response)
            self.assertEqual("postgres", body["database"])
            self.assertTrue(body["destination_configured"])
        finally:
            server.shutdown()
            server.server_close()

    def test_dashboard_events_require_auth_and_dead_event_can_be_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(f"{directory}/dashboard.db")
            event_id, _ = store.ingest("crm", "failed", {"private": "not returned"})
            store.claim()
            store.fail(event_id, 5, "temporary failure")
            runtime = SimpleNamespace(
                api_token="operator-token",
                store=store,
                health=lambda: {"status": "ok"},
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(runtime))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/") as response:
                    self.assertIn("SyncBridge Console", response.read().decode())
                with self.assertRaises(HTTPError) as denied:
                    urlopen(base + "/api/events")
                self.assertEqual(401, denied.exception.code)
                headers = {"Authorization": "Bearer operator-token"}
                with urlopen(Request(base + "/api/events", headers=headers)) as response:
                    body = json.load(response)
                self.assertEqual("dead", body["events"][0]["status"])
                self.assertNotIn("payload", body["events"][0])
                retry = Request(base + f"/api/events/{event_id}/retry", headers=headers, method="POST")
                with urlopen(retry) as response:
                    self.assertEqual("retry", json.load(response)["status"])
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()

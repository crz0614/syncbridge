import json
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

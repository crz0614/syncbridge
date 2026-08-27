import os
import uuid
import unittest

from syncbridge.postgres_store import PostgresStore


@unittest.skipUnless(os.getenv("DATABASE_URL"), "DATABASE_URL not configured")
class PostgresIntegrationTests(unittest.TestCase):
    def test_concurrent_safe_claim_path(self):
        store = PostgresStore(os.environ["DATABASE_URL"])
        key = str(uuid.uuid4())
        event_id, created = store.ingest("ci", key, {"verified": True})
        self.assertTrue(created)
        event = store.claim()
        self.assertEqual(event_id, event["id"])
        store.finish(event_id)


if __name__ == "__main__":
    unittest.main()

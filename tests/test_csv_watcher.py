import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from syncbridge.csv_ingest import import_csv, process_watched_file
from syncbridge.store import Store


class CSVWatcherTests(unittest.TestCase):
    def test_success_is_archived_without_name_overwrite_and_remains_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(str(root / "queue.db"))
            for _ in range(2):
                incoming = root / "contacts.csv"
                incoming.write_text("email,name\na@example.com,A\n", encoding="utf-8")
                result = process_watched_file(store, root, incoming)
                self.assertFalse(incoming.exists())
            self.assertEqual(result, {"created": 0, "duplicates": 1})
            self.assertEqual(len(list((root / ".syncbridge-processed").glob("*/contacts.csv"))), 2)

    def test_bad_file_is_quarantined_without_stopping_later_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(str(root / "queue.db"))
            bad = root / "bad.csv"
            bad.write_text("email,email\na@x,b@x\n", encoding="utf-8")
            self.assertEqual(process_watched_file(store, root, bad), "quarantined")
            self.assertEqual(len(list((root / ".syncbridge-failed").glob("*/bad.csv"))), 1)
            good = root / "good.csv"
            good.write_text("email\na@x\n", encoding="utf-8")
            self.assertEqual(process_watched_file(store, root, good)["created"], 1)

    def test_transient_failure_requeues_without_overwriting_new_arrival(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "contacts.csv"
            original.write_text("email\nold@example.com\n", encoding="utf-8")
            def fail_after_new_arrival(*args, **kwargs):
                original.write_text("email\nnew@example.com\n", encoding="utf-8")
                raise RuntimeError("database unavailable")
            with patch("syncbridge.csv_ingest.import_csv", side_effect=fail_after_new_arrival):
                self.assertEqual(process_watched_file(object(), root, original), "retry")
            self.assertIn("new@example.com", original.read_text())
            retry = next((root / ".syncbridge-retry").glob("*/contacts.csv"))
            self.assertIn("old@example.com", retry.read_text())

    def test_lost_claim_is_benign(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "already-claimed.csv"
            self.assertEqual(process_watched_file(object(), root, missing), "claimed_elsewhere")

    def test_concurrent_watchers_claim_once_and_leave_new_arrival(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "contacts.csv"
            path.write_text("email\nold@example.com\n", encoding="utf-8")
            entered, release = threading.Event(), threading.Event()
            outcomes = []
            def importer(store, claimed, **kwargs):
                entered.set()
                self.assertTrue(release.wait(5))
                self.assertIn("old@example.com", Path(claimed).read_text())
                return {"created": 1, "duplicates": 0}
            with patch("syncbridge.csv_ingest.import_csv", side_effect=importer) as imported:
                first = threading.Thread(target=lambda: outcomes.append(process_watched_file(object(), root, path)))
                first.start()
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(process_watched_file(object(), root, path), "claimed_elsewhere")
                    path.write_text("email\nnew@example.com\n", encoding="utf-8")
                finally:
                    release.set()
                    first.join(5)
                self.assertFalse(first.is_alive())
                self.assertEqual(outcomes, [{"created": 1, "duplicates": 0}])
                self.assertEqual(imported.call_count, 1)
            self.assertIn("new@example.com", path.read_text())

    def test_partial_database_failure_resumes_using_legacy_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(str(root / "queue.db"))
            path = root / "contacts.csv"
            path.write_text("email\na@example.com\nb@example.com\n", encoding="utf-8")
            ingest = store.ingest
            calls = 0
            def flaky(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("temporarily unavailable")
                return ingest(*args, **kwargs)
            with patch.object(store, "ingest", side_effect=flaky):
                self.assertEqual(process_watched_file(store, root, path), "retry")
            retry = next((root / ".syncbridge-retry").glob("*/contacts.csv"))
            self.assertEqual(process_watched_file(store, root, retry), {"created": 1, "duplicates": 1})
            # Legacy direct imports use precisely the same root path identity.
            path.write_text("email\na@example.com\nb@example.com\n", encoding="utf-8")
            self.assertEqual(import_csv(store, str(path)), {"created": 0, "duplicates": 2})

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from syncbridge.csv_ingest import import_csv
from syncbridge.store import Store


class CSVValidationTests(unittest.TestCase):
    def test_invalid_files_never_write_even_when_bad_record_is_last(self):
        invalid = (
            "", "\n", "id,\n1,x\n", "id,  \n1,x\n",
            "id,id\n1,2\n", "id,name\n1,good\n2\n",
            "id,name\n1,good\n2,secret,extra\n",
            'id,name\n1,good\n2,"unterminated',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            for content in invalid:
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    store = Mock()
                    with self.assertRaises(ValueError) as error:
                        import_csv(store, str(path))
                    store.ingest.assert_not_called()
                    self.assertNotIn("secret", str(error.exception))
            path.write_bytes(b"id,name\n1,good\n2,\xff\n")
            store = Mock()
            with self.assertRaises(ValueError):
                import_csv(store, str(path))
            store.ingest.assert_not_called()

    def test_valid_bom_multiline_and_blank_rows_keep_legacy_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text('\ufeffid,name\n1,"A\nB"\n\n2,"C, D"\n', encoding="utf-8")
            store = Store(f"{directory}/data.db")
            row = {"id": "1", "name": "A\nB"}
            old_key = hashlib.sha256((str(path.resolve()) + ":2:" + repr(sorted(row.items()))).encode()).hexdigest()
            store.ingest("csv", old_key, row)
            self.assertEqual(import_csv(store, str(path)), {"created": 1, "duplicates": 1})
            self.assertEqual(import_csv(store, str(path)), {"created": 0, "duplicates": 2})
            self.assertEqual(store.stats(), {"pending": 2})

    def test_snapshot_is_not_reopened_after_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text("id,name\n1,first\n2,second\n", encoding="utf-8")
            seen = []

            def ingest(source, key, payload):
                seen.append(payload)
                path.write_text("id,name\n9,replaced\n", encoding="utf-8")
                return len(seen), True

            import_csv(Mock(ingest=ingest), str(path))
            self.assertEqual(seen, [{"id": "1", "name": "first"}, {"id": "2", "name": "second"}])

    def test_database_failure_is_resumable_not_claimed_as_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text("id,name\n1,first\n2,second\n", encoding="utf-8")
            store = Store(f"{directory}/data.db")
            original = store.ingest
            calls = 0

            def fail_second(*args):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("database unavailable")
                return original(*args)

            store.ingest = fail_second
            with self.assertRaises(OSError):
                import_csv(store, str(path))
            store.ingest = original
            self.assertEqual(import_csv(store, str(path)), {"created": 1, "duplicates": 1})

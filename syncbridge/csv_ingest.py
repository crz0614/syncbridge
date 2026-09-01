from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import time
from pathlib import Path

from .mapping import FieldMap


def import_csv(store, path: str, source: str = "csv", field_map: FieldMap | None = None):
    file_path = Path(path)
    mapper = field_map or FieldMap()
    created = duplicates = 0
    # Validate a stable snapshot before any ingestion. Spill large inputs to disk
    # instead of retaining all customer rows in memory or reopening a changed file.
    with tempfile.SpooledTemporaryFile(mode="w+t", encoding="utf-8", max_size=1_048_576) as snapshot:
        with file_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle, strict=True)
            try:
                headers = next(reader, None)
                if not headers or any(not name.strip() for name in headers):
                    raise ValueError("CSV requires non-empty column names")
                if len(set(headers)) != len(headers):
                    raise ValueError("CSV column names must be unique")
                record_number = 1
                for values in reader:
                    if not values:  # Match DictReader's historical blank-line handling.
                        continue
                    record_number += 1
                    if len(values) != len(headers):
                        raise ValueError(f"CSV record {record_number} has an invalid column count")
                    row = dict(zip(headers, values))
                    snapshot.write(json.dumps([record_number, row]) + "\n")
            except (csv.Error, UnicodeError):
                raise ValueError("CSV must contain valid UTF-8 and well-formed quoting") from None
        snapshot.seek(0)
        for entry in snapshot:
            line_number, row = json.loads(entry)
            payload = mapper.apply(dict(row))
            key = hashlib.sha256(
                (str(file_path.resolve()) + ":" + str(line_number) + ":" + repr(sorted(row.items()))).encode()
            ).hexdigest()
            _, was_created = store.ingest(source, key, payload)
            created += int(was_created)
            duplicates += int(not was_created)
    return {"created": created, "duplicates": duplicates}


def watch_directory(store, directory: str, interval: int, field_map: FieldMap | None = None):
    root = Path(directory)
    processed = root / ".syncbridge-processed"
    processed.mkdir(parents=True, exist_ok=True)
    while True:
        for path in sorted(root.glob("*.csv")):
            import_csv(store, str(path), field_map=field_map)
            path.rename(processed / path.name)
        time.sleep(interval)

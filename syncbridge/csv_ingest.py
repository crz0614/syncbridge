from __future__ import annotations

import csv
import hashlib
import time
from pathlib import Path

from .mapping import FieldMap


def import_csv(store, path: str, source: str = "csv", field_map: FieldMap | None = None):
    file_path = Path(path)
    mapper = field_map or FieldMap()
    created = duplicates = 0
    with file_path.open(newline="", encoding="utf-8-sig") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
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

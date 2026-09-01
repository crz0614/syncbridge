from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import time
import uuid
from pathlib import Path

from .mapping import FieldMap


def import_csv(store, path: str, source: str = "csv", field_map: FieldMap | None = None, identity_path: str | None = None):
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
                (str(Path(identity_path or path).resolve()) + ":" + str(line_number) + ":" + repr(sorted(row.items()))).encode()
            ).hexdigest()
            _, was_created = store.ingest(source, key, payload)
            created += int(was_created)
            duplicates += int(not was_created)
    return {"created": created, "duplicates": duplicates}


def process_watched_file(store, root: Path, path: Path, field_map: FieldMap | None = None, identity_name: str | None = None):
    """Atomically claim one file and retain every failure for a safe retry or review."""
    processed = root / ".syncbridge-processed"
    failed = root / ".syncbridge-failed"
    retry = root / ".syncbridge-retry"
    for directory in (processed, failed, retry):
        directory.mkdir(parents=True, exist_ok=True)
    original_name = identity_name or path.name
    token = uuid.uuid4().hex
    claim_dir = processed / f".inflight-{token}"
    claim_dir.mkdir()
    claimed = claim_dir / original_name
    try:
        path.rename(claimed)  # Same-filesystem rename: only one watcher can claim it.
    except FileNotFoundError:
        claim_dir.rmdir()
        return "claimed_elsewhere"
    try:
        result = import_csv(store, str(claimed), field_map=field_map, identity_path=str(root / original_name))
    except ValueError:
        claim_dir.rename(failed / token)
        return "quarantined"
    except Exception:
        # Never overwrite a newly arrived file. Retry subdirectories preserve the
        # original basename, so the legacy identity key remains stable next pass.
        claim_dir.rename(retry / token)
        return "retry"
    claim_dir.rename(processed / token)
    return result


def watch_directory(store, directory: str, interval: int, field_map: FieldMap | None = None):
    root = Path(directory)
    retry = root / ".syncbridge-retry"
    retry.mkdir(parents=True, exist_ok=True)
    while True:
        for path in sorted(root.glob("*.csv")):
            process_watched_file(store, root, path, field_map)
        for path in sorted(retry.glob("*/*.csv")):
            process_watched_file(store, root, path, field_map, path.name)
            try:
                path.parent.rmdir()
            except OSError:
                pass
        time.sleep(interval)

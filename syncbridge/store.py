from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL,
  last_error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(source, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_events_ready
ON events(status, next_attempt_at);
"""


class Store:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def ingest(self, source: str, key: str, payload: dict) -> tuple[int, bool]:
        now = int(time.time())
        with self.connect() as db:
            before = db.total_changes
            db.execute(
                """INSERT OR IGNORE INTO events
                (source,idempotency_key,payload,next_attempt_at,created_at,updated_at)
                VALUES (?,?,?,?,?,?)""",
                (source, key, json.dumps(payload, separators=(",", ":")), now, now, now),
            )
            created = db.total_changes > before
            row = db.execute(
                "SELECT id FROM events WHERE source=? AND idempotency_key=?", (source, key)
            ).fetchone()
            return int(row["id"]), created

    def claim(self):
        now = int(time.time())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM events WHERE status IN ('pending','retry')
                AND next_attempt_at<=? ORDER BY id LIMIT 1""", (now,)
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE events SET status='processing', attempts=attempts+1, updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            return dict(row)

    def finish(self, event_id: int):
        with self.connect() as db:
            db.execute(
                "UPDATE events SET status='done',last_error=NULL,updated_at=? WHERE id=?",
                (int(time.time()), event_id),
            )

    def fail(self, event_id: int, attempts: int, error: str, max_attempts: int = 5):
        status = "dead" if attempts >= max_attempts else "retry"
        delay = min(3600, 2 ** max(1, attempts) * 5)
        now = int(time.time())
        with self.connect() as db:
            db.execute(
                """UPDATE events SET status=?,last_error=?,next_attempt_at=?,updated_at=?
                WHERE id=?""",
                (status, error[:1000], now + delay, now, event_id),
            )

    def stats(self):
        with self.connect() as db:
            return {r["status"]: r["n"] for r in db.execute(
                "SELECT status,COUNT(*) n FROM events GROUP BY status"
            )}

    def list_events(self, limit: int = 100):
        limit = max(1, min(int(limit), 500))
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                """SELECT id,source,idempotency_key,status,attempts,last_error,
                created_at,updated_at FROM events ORDER BY id DESC LIMIT ?""",
                (limit,),
            )]

    def retry(self, event_id: int) -> bool:
        now = int(time.time())
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE events SET status='retry',next_attempt_at=?,last_error=NULL,
                updated_at=? WHERE id=? AND status IN ('dead','retry')""",
                (now, now, event_id),
            )
            return cursor.rowcount == 1

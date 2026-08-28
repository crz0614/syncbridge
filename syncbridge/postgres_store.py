from __future__ import annotations

import json
import time


SCHEMA = """
CREATE TABLE IF NOT EXISTS syncbridge_events (
  id BIGSERIAL PRIMARY KEY, source TEXT NOT NULL, idempotency_key TEXT NOT NULL,
  payload JSONB NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at BIGINT NOT NULL, last_error TEXT, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
  UNIQUE(source,idempotency_key)
);
CREATE INDEX IF NOT EXISTS syncbridge_events_ready ON syncbridge_events(status,next_attempt_at);
"""


class PostgresStore:
    def __init__(self, dsn: str):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL mode requires: pip install 'syncbridge[postgres]'") from exc
        self.psycopg, self.dict_row, self.dsn = psycopg, dict_row, dsn
        with self.connect() as db:
            db.execute(SCHEMA)

    def connect(self):
        return self.psycopg.connect(self.dsn, row_factory=self.dict_row)

    def ingest(self, source: str, key: str, payload: dict):
        now = int(time.time())
        with self.connect() as db:
            row = db.execute("""INSERT INTO syncbridge_events
                (source,idempotency_key,payload,next_attempt_at,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(source,idempotency_key) DO NOTHING RETURNING id""",
                (source, key, json.dumps(payload), now, now, now)).fetchone()
            if row:
                return int(row["id"]), True
            row = db.execute("SELECT id FROM syncbridge_events WHERE source=%s AND idempotency_key=%s", (source, key)).fetchone()
            return int(row["id"]), False

    def claim(self):
        now = int(time.time())
        with self.connect() as db:
            row = db.execute("""SELECT * FROM syncbridge_events WHERE status IN ('pending','retry')
                AND next_attempt_at<=%s ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1""", (now,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE syncbridge_events SET status='processing',attempts=attempts+1,updated_at=%s WHERE id=%s", (now, row["id"]))
            return row

    def finish(self, event_id: int):
        with self.connect() as db:
            db.execute("UPDATE syncbridge_events SET status='done',last_error=NULL,updated_at=%s WHERE id=%s", (int(time.time()), event_id))

    def fail(self, event_id: int, attempts: int, error: str, max_attempts: int = 5):
        status, now = ("dead" if attempts >= max_attempts else "retry"), int(time.time())
        with self.connect() as db:
            db.execute("""UPDATE syncbridge_events SET status=%s,last_error=%s,next_attempt_at=%s,updated_at=%s WHERE id=%s""",
                (status, error[:1000], now + min(3600, 2 ** max(1, attempts) * 5), now, event_id))

    def stats(self):
        with self.connect() as db:
            return {r["status"]: r["n"] for r in db.execute("SELECT status,COUNT(*) n FROM syncbridge_events GROUP BY status")}

    def list_events(self, limit: int = 100):
        limit = max(1, min(int(limit), 500))
        with self.connect() as db:
            return list(db.execute("""SELECT id,source,idempotency_key,status,attempts,last_error,
                created_at,updated_at FROM syncbridge_events ORDER BY id DESC LIMIT %s""", (limit,)))

    def retry(self, event_id: int) -> bool:
        now = int(time.time())
        with self.connect() as db:
            result = db.execute("""UPDATE syncbridge_events SET status='retry',next_attempt_at=%s,
                last_error=NULL,updated_at=%s WHERE id=%s AND status IN ('dead','retry')""",
                (now, now, event_id))
            return result.rowcount == 1

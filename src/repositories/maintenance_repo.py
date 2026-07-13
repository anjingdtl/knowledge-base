"""SQLite persistence for the Maintenance Control Plane.

The store owns workflow state only. Canonical Claim and Page content remain in
``WikiRepository``; JSON payloads below contain IDs, plans and audit context.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

_DDL = """
CREATE TABLE IF NOT EXISTS maintenance_source_events (
    event_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    knowledge_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    source_path TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_maintenance_events_knowledge ON maintenance_source_events(knowledge_id, created_at);

CREATE TABLE IF NOT EXISTS maintenance_jobs (
    job_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    status TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    created_at TEXT NOT NULL,
    lease_until TEXT,
    due_at TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_maintenance_jobs_status_due ON maintenance_jobs(status, due_at, created_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_jobs_lease ON maintenance_jobs(lease_until);

CREATE TABLE IF NOT EXISTS maintenance_reviews (
    review_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    job_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_maintenance_reviews_status ON maintenance_reviews(status, created_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_reviews_job ON maintenance_reviews(job_id);

CREATE TABLE IF NOT EXISTS maintenance_dead_letters (
    job_id TEXT PRIMARY KEY,
    failed_at TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS maintenance_health_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_maintenance_health_captured ON maintenance_health_snapshots(captured_at);
CREATE TABLE IF NOT EXISTS maintenance_schedules (
    schedule_name TEXT PRIMARY KEY,
    next_run_at TEXT,
    lease_until TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
"""


class MaintenanceRepository:
    """Small transactional repository; no in-process state is authoritative."""

    def __init__(self, database: Any) -> None:
        self._db = database
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self._db.get_conn())

    def _ensure_schema(self) -> None:
        conn = self._conn()
        conn.executescript(_DDL)
        conn.commit()

    @staticmethod
    def _dump(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _load(value: str) -> dict[str, Any]:
        return json.loads(value) if value else {}

    def record_source_event(self, event: dict[str, Any]) -> bool:
        """Insert once by revision-aware key; return False for a duplicate."""
        try:
            self._conn().execute(
                """INSERT INTO maintenance_source_events
                   (event_id, idempotency_key, event_type, knowledge_id, source_revision,
                    source_path, correlation_id, created_at, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["event_id"], event["idempotency_key"], event["event_type"],
                    event["knowledge_id"], event["source_revision"], event.get("source_path", ""),
                    event.get("correlation_id", ""), event["created_at"], self._dump(event),
                ),
            )
            self._conn().commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def save_job(self, job: dict[str, Any]) -> None:
        self._conn().execute(
            """INSERT INTO maintenance_jobs(job_id, idempotency_key, status, risk_level, created_at, payload_json)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, risk_level=excluded.risk_level,
               idempotency_key=excluded.idempotency_key, payload_json=excluded.payload_json""",
            (job["job_id"], job.get("idempotency_key") or None, job["status"], job["risk_level"], job["created_at"], self._dump(job)),
        )
        self._conn().commit()
        if job.get("status") == "dead_letter":
            self._conn().execute(
                "INSERT OR REPLACE INTO maintenance_dead_letters(job_id, failed_at, last_error, payload_json) VALUES (?, ?, ?, ?)",
                (job["job_id"], job.get("finished_at") or job["created_at"], job.get("error", ""), self._dump(job)),
            )
            self._conn().commit()
        else:
            self._conn().execute("DELETE FROM maintenance_dead_letters WHERE job_id = ?", (job["job_id"],))
            self._conn().commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._conn().execute("SELECT payload_json FROM maintenance_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._load(row[0]) if row else None

    def find_job_by_idempotency(self, key: str) -> dict[str, Any] | None:
        row = self._conn().execute("SELECT payload_json FROM maintenance_jobs WHERE idempotency_key = ?", (key,)).fetchone()
        return self._load(row[0]) if row else None

    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT payload_json FROM maintenance_jobs"
        args: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            args = (status,)
        query += " ORDER BY created_at DESC LIMIT ?"
        rows = self._conn().execute(query, (*args, limit)).fetchall()
        return [self._load(row[0]) for row in rows]

    def claim_next_job(self, *, worker_id: str, now: str, lease_until: str) -> dict[str, Any] | None:
        """Atomically lease one due pending/retry job for a future worker.

        Phase 4 supplies execution; Phase 3 establishes the database lease
        primitive and ensures two workers cannot acquire the same job.
        """
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT job_id, payload_json FROM maintenance_jobs
                   WHERE status IN ('pending', 'retry_wait')
                     AND (due_at IS NULL OR due_at <= ?)
                   ORDER BY created_at ASC LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            job = self._load(row[1])
            job.update({"status": "leased", "lease_until": lease_until, "worker_id": worker_id})
            conn.execute(
                "UPDATE maintenance_jobs SET status = 'leased', lease_until = ?, payload_json = ? WHERE job_id = ?",
                (lease_until, self._dump(job), row[0]),
            )
            conn.commit()
            return job
        except Exception:
            conn.rollback()
            raise

    def recover_expired_leases(self, *, now: str) -> int:
        """Return abandoned leases to pending so a restarted worker can resume."""
        rows = self._conn().execute(
            "SELECT job_id, payload_json FROM maintenance_jobs WHERE status = 'leased' AND lease_until < ?", (now,),
        ).fetchall()
        for row in rows:
            job = self._load(row[1])
            job.update({"status": "pending", "lease_until": None, "worker_id": ""})
            self._conn().execute(
                "UPDATE maintenance_jobs SET status = 'pending', lease_until = NULL, payload_json = ? WHERE job_id = ?",
                (self._dump(job), row[0]),
            )
        self._conn().commit()
        return len(rows)

    def save_review(self, review: dict[str, Any]) -> None:
        self._conn().execute(
            """INSERT INTO maintenance_reviews(review_id, status, risk_level, job_id, created_at, payload_json)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(review_id) DO UPDATE SET status=excluded.status, payload_json=excluded.payload_json""",
            (review["review_id"], review["status"], review["risk_level"], review.get("job_id", ""), review["created_at"], self._dump(review)),
        )
        self._conn().commit()

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        row = self._conn().execute("SELECT payload_json FROM maintenance_reviews WHERE review_id = ?", (review_id,)).fetchone()
        return self._load(row[0]) if row else None

    def list_reviews(self, status: str | None = "open", review_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn().execute("SELECT payload_json FROM maintenance_reviews ORDER BY created_at DESC").fetchall()
        records = [self._load(row[0]) for row in rows]
        if status:
            records = [record for record in records if record.get("status") == status]
        if review_type:
            records = [record for record in records if record.get("review_type") == review_type]
        return records[:limit]

    def save_health_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._conn().execute(
            "INSERT INTO maintenance_health_snapshots(snapshot_id, captured_at, payload_json) VALUES (?, ?, ?)",
            (snapshot["snapshot_id"], snapshot["captured_at"], self._dump(snapshot)),
        )
        self._conn().commit()

    def list_dead_letters(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT payload_json FROM maintenance_dead_letters ORDER BY failed_at DESC LIMIT ?", (limit,),
        ).fetchall()
        return [self._load(row[0]) for row in rows]

    def claim_schedule(self, name: str, *, now: str, lease_until: str) -> bool:
        """Acquire a cross-process schedule lease exactly once."""
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT lease_until FROM maintenance_schedules WHERE schedule_name = ?", (name,)).fetchone()
            if row is not None and row[0] and row[0] >= now:
                conn.commit()
                return False
            conn.execute(
                "INSERT INTO maintenance_schedules(schedule_name, next_run_at, lease_until, payload_json) VALUES (?, ?, ?, '{}') "
                "ON CONFLICT(schedule_name) DO UPDATE SET next_run_at=excluded.next_run_at, lease_until=excluded.lease_until",
                (name, now, lease_until),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise

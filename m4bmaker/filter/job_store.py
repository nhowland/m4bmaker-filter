"""SQLite-backed Job Orchestrator persistence (PRD §14.2, §14.3; ADR-0005).

See ``docs/adr/0005-job-orchestrator-persistence.md`` for the schema and
the rationale behind the SQLite/JSON split: this module stores job state,
transition history, and per-chunk transcription-durability tracking only —
never transcript *content*, which stays in the JSON artifacts
``transcript.py`` reads and writes.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .jobs import JobState, JobType, validate_transition

_SCHEMA_VERSION = "1"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    progress_message TEXT NOT NULL DEFAULT '',
    progress_fraction REAL,
    error_code TEXT,
    error_message TEXT,
    resource_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    timestamp TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS transcription_chunks (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    chunk_index INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    owned_start_ms INTEGER NOT NULL,
    owned_end_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    committed_at TEXT,
    PRIMARY KEY (job_id, chunk_index)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class JobRecord:
    id: str
    job_type: JobType
    state: JobState
    created_at: str
    updated_at: str
    progress_message: str
    progress_fraction: float | None
    error_code: str | None
    error_message: str | None
    resource: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkRecord:
    job_id: str
    chunk_index: int
    start_ms: int
    end_ms: int
    owned_start_ms: int
    owned_end_ms: int
    status: str
    committed_at: str | None


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating if absent) the job database at *db_path* and ensure
    its schema exists. ``db_path.parent`` is created if missing, matching
    the rest of this codebase's storage helpers."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) "
            "VALUES ('schema_version', ?)",
            (_SCHEMA_VERSION,),
        )
    return conn


class JobStore:
    """Thin wrapper around a job database connection.

    Every mutating method commits its own transaction (via ``with
    self._conn:``) — callers never need to manage transactions themselves,
    and a raised exception rolls back automatically (sqlite3's own
    context-manager behavior).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_job(
        self, job_id: str, job_type: JobType, resource: dict[str, Any] | None = None
    ) -> JobRecord:
        """Create a new job in state ``QUEUED``."""
        now = _now()
        resource_json = json.dumps(resource or {})
        with self._conn:
            self._conn.execute(
                "INSERT INTO jobs "
                "(id, job_type, state, created_at, updated_at, resource_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    job_type.value,
                    JobState.QUEUED.value,
                    now,
                    now,
                    resource_json,
                ),
            )
            self._conn.execute(
                "INSERT INTO job_events "
                "(job_id, timestamp, from_state, to_state, message) "
                "VALUES (?, ?, NULL, ?, ?)",
                (job_id, now, JobState.QUEUED.value, "Job created."),
            )
        job = self.get_job(job_id)
        assert job is not None  # just inserted, must exist
        return job

    def get_job(self, job_id: str) -> JobRecord | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_jobs(self, job_type: JobType | None = None) -> list[JobRecord]:
        if job_type is None:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE job_type = ? ORDER BY created_at",
                (job_type.value,),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def transition(
        self, job_id: str, to_state: JobState, message: str = ""
    ) -> JobRecord:
        """Move *job_id* to *to_state*, validating the transition against
        ``jobs.ALLOWED_TRANSITIONS`` first. Raises
        :class:`~m4bmaker.filter.jobs.InvalidJobTransition` — and writes
        nothing — if the transition is not allowed."""
        current = self.get_job(job_id)
        if current is None:
            raise KeyError(f"Unknown job_id: {job_id!r}")
        validate_transition(current.state, to_state)

        now = _now()
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET state = ?, updated_at = ?, progress_message = ? "
                "WHERE id = ?",
                (to_state.value, now, message, job_id),
            )
            self._conn.execute(
                "INSERT INTO job_events "
                "(job_id, timestamp, from_state, to_state, message) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, now, current.state.value, to_state.value, message),
            )
        job = self.get_job(job_id)
        assert job is not None
        return job

    def set_error(self, job_id: str, error_code: str, error_message: str) -> None:
        """Record error details on a job without changing its state — the
        caller separately calls :meth:`transition` to move it to
        ``FAILED``/``NEEDS_ATTENTION``. Kept as a separate call so the error
        detail and the state transition are each explicit, not bundled
        into one method with an implicit branch."""
        now = _now()
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET error_code = ?, error_message = ?, updated_at = ? "
                "WHERE id = ?",
                (error_code, error_message, now, job_id),
            )

    def update_progress(
        self, job_id: str, message: str, fraction: float | None = None
    ) -> None:
        """Update progress fields without touching state or writing a
        job_events row — progress ticks happen far more often than state
        transitions and would otherwise flood the event log."""
        now = _now()
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET progress_message = ?, progress_fraction = ?, "
                "updated_at = ? WHERE id = ?",
                (message, fraction, now, job_id),
            )

    def commit_chunk(
        self,
        job_id: str,
        chunk_index: int,
        start_ms: int,
        end_ms: int,
        owned_start_ms: int,
        owned_end_ms: int,
    ) -> None:
        """Mark chunk *chunk_index* of *job_id* as committed.

        Idempotent via ``INSERT OR REPLACE`` — re-committing the same
        chunk index (e.g. a crash right after this call but before the
        caller could confirm it landed) is safe and simply overwrites with
        identical data, rather than raising a primary-key conflict.
        """
        now = _now()
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO transcription_chunks "
                "(job_id, chunk_index, start_ms, end_ms, owned_start_ms, "
                "owned_end_ms, status, committed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    chunk_index,
                    start_ms,
                    end_ms,
                    owned_start_ms,
                    owned_end_ms,
                    "completed",
                    now,
                ),
            )

    def list_committed_chunks(self, job_id: str) -> list[ChunkRecord]:
        rows = self._conn.execute(
            "SELECT * FROM transcription_chunks WHERE job_id = ? "
            "AND status = 'completed' ORDER BY chunk_index",
            (job_id,),
        ).fetchall()
        return [
            ChunkRecord(
                job_id=r["job_id"],
                chunk_index=r["chunk_index"],
                start_ms=r["start_ms"],
                end_ms=r["end_ms"],
                owned_start_ms=r["owned_start_ms"],
                owned_end_ms=r["owned_end_ms"],
                status=r["status"],
                committed_at=r["committed_at"],
            )
            for r in rows
        ]

    def is_chunk_committed(self, job_id: str, chunk_index: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM transcription_chunks WHERE job_id = ? AND chunk_index = ? "
            "AND status = 'completed'",
            (job_id, chunk_index),
        ).fetchone()
        return row is not None

    def _row_to_job(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            job_type=JobType(row["job_type"]),
            state=JobState(row["state"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            progress_message=row["progress_message"],
            progress_fraction=row["progress_fraction"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            resource=json.loads(row["resource_json"]),
        )

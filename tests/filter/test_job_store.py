"""Tests for m4bmaker.filter.job_store — SQLite job persistence (PRD §14.2,
§14.3; ADR-0005).

Uses a real SQLite database in a temp file for every test (not mocked) —
standard practice for testing SQLite-backed code, and fast enough (a fresh
in-file DB per test) not to need mocking's speed benefit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from m4bmaker.filter.job_store import JobStore, connect
from m4bmaker.filter.jobs import InvalidJobTransition, JobState, JobType


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    conn = connect(tmp_path / "filter.db")
    return JobStore(conn)


class TestConnect:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "filter.db"
        connect(db_path)
        assert db_path.is_file()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nested" / "dir" / "filter.db"
        connect(db_path)
        assert db_path.is_file()

    def test_records_schema_version(self, tmp_path: Path) -> None:
        conn = connect(tmp_path / "filter.db")
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row[0] == "1"

    def test_idempotent_reconnect(self, tmp_path: Path) -> None:
        db_path = tmp_path / "filter.db"
        connect(db_path).close()
        conn2 = connect(db_path)  # must not raise on existing schema
        row = conn2.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row[0] == "1"


class TestCreateJob:
    def test_starts_in_queued_state(self, store: JobStore) -> None:
        job = store.create_job("job-1", JobType.TRANSCRIPTION)
        assert job.state is JobState.QUEUED
        assert job.job_type is JobType.TRANSCRIPTION

    def test_stores_and_returns_resource_dict(self, store: JobStore) -> None:
        job = store.create_job(
            "job-1", JobType.TRANSCRIPTION, resource={"source_path": "/books/a.m4b"}
        )
        assert job.resource == {"source_path": "/books/a.m4b"}

    def test_defaults_to_empty_resource(self, store: JobStore) -> None:
        job = store.create_job("job-1", JobType.MODEL_DOWNLOAD)
        assert job.resource == {}

    def test_records_creation_event(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        rows = store._conn.execute(
            "SELECT * FROM job_events WHERE job_id = ?", ("job-1",)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["to_state"] == "QUEUED"
        assert rows[0]["from_state"] is None


class TestGetJob:
    def test_returns_none_for_unknown_job(self, store: JobStore) -> None:
        assert store.get_job("nonexistent") is None

    def test_round_trips_created_job(self, store: JobStore) -> None:
        created = store.create_job("job-1", JobType.SCAN)
        fetched = store.get_job("job-1")
        assert fetched == created


class TestListJobs:
    def test_lists_all_jobs_when_no_filter(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.create_job("job-2", JobType.RENDER)
        jobs = store.list_jobs()
        assert {j.id for j in jobs} == {"job-1", "job-2"}

    def test_filters_by_job_type(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.create_job("job-2", JobType.RENDER)
        jobs = store.list_jobs(job_type=JobType.RENDER)
        assert [j.id for j in jobs] == ["job-2"]


class TestTransition:
    def test_valid_transition_updates_state(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        updated = store.transition("job-1", JobState.PREPARING, "Verifying source...")
        assert updated.state is JobState.PREPARING
        assert updated.progress_message == "Verifying source..."

    def test_valid_transition_records_event(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.transition("job-1", JobState.PREPARING)
        rows = store._conn.execute(
            "SELECT * FROM job_events WHERE job_id = ? ORDER BY id", ("job-1",)
        ).fetchall()
        assert len(rows) == 2  # creation + this transition
        assert rows[1]["from_state"] == "QUEUED"
        assert rows[1]["to_state"] == "PREPARING"

    def test_invalid_transition_raises_and_writes_nothing(
        self, store: JobStore
    ) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        with pytest.raises(InvalidJobTransition):
            store.transition("job-1", JobState.COMPLETED)  # QUEUED -> COMPLETED invalid
        job = store.get_job("job-1")
        assert job is not None
        assert job.state is JobState.QUEUED  # unchanged
        events = store._conn.execute(
            "SELECT * FROM job_events WHERE job_id = ?", ("job-1",)
        ).fetchall()
        assert len(events) == 1  # only the creation event

    def test_unknown_job_raises_key_error(self, store: JobStore) -> None:
        with pytest.raises(KeyError):
            store.transition("nonexistent", JobState.PREPARING)


class TestSetError:
    def test_sets_error_fields_without_changing_state(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.set_error("job-1", "MODEL_NOT_FOUND", "whisper-cli binary missing")
        job = store.get_job("job-1")
        assert job is not None
        assert job.state is JobState.QUEUED
        assert job.error_code == "MODEL_NOT_FOUND"
        assert job.error_message == "whisper-cli binary missing"


class TestUpdateProgress:
    def test_updates_message_and_fraction(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.update_progress("job-1", "Transcribing chunk 3/10", 0.3)
        job = store.get_job("job-1")
        assert job is not None
        assert job.progress_message == "Transcribing chunk 3/10"
        assert job.progress_fraction == 0.3

    def test_does_not_write_a_job_event(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.update_progress("job-1", "50%", 0.5)
        events = store._conn.execute(
            "SELECT * FROM job_events WHERE job_id = ?", ("job-1",)
        ).fetchall()
        assert len(events) == 1  # only creation — no event flood from progress ticks


class TestChunkTracking:
    def test_commit_and_list(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.commit_chunk("job-1", 0, 0, 30_000, 0, 25_000)
        store.commit_chunk("job-1", 1, 25_000, 55_000, 25_000, 55_000)
        chunks = store.list_committed_chunks("job-1")
        assert [c.chunk_index for c in chunks] == [0, 1]
        assert chunks[0].owned_end_ms == 25_000

    def test_is_chunk_committed(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        assert store.is_chunk_committed("job-1", 0) is False
        store.commit_chunk("job-1", 0, 0, 30_000, 0, 25_000)
        assert store.is_chunk_committed("job-1", 0) is True
        assert store.is_chunk_committed("job-1", 1) is False

    def test_recommitting_same_chunk_is_idempotent(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.commit_chunk("job-1", 0, 0, 30_000, 0, 25_000)
        store.commit_chunk(
            "job-1", 0, 0, 30_000, 0, 25_000
        )  # re-commit, must not error
        chunks = store.list_committed_chunks("job-1")
        assert len(chunks) == 1

    def test_chunks_scoped_to_their_own_job(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.create_job("job-2", JobType.TRANSCRIPTION)
        store.commit_chunk("job-1", 0, 0, 30_000, 0, 25_000)
        assert store.list_committed_chunks("job-2") == []


class TestFullPauseResumeLifecycle:
    def test_realistic_state_and_chunk_progression(self, store: JobStore) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION, resource={"model": "base.en"})

        store.transition("job-1", JobState.PREPARING)
        store.transition("job-1", JobState.RUNNING)
        store.commit_chunk("job-1", 0, 0, 30_000, 0, 25_000)
        store.update_progress("job-1", "Chunk 1/3", 0.33)

        # User requests pause.
        store.transition("job-1", JobState.PAUSING)
        store.transition("job-1", JobState.PAUSED)

        paused = store.get_job("job-1")
        assert paused is not None
        assert paused.state is JobState.PAUSED
        assert len(store.list_committed_chunks("job-1")) == 1

        # App restarts; User explicitly resumes.
        store.transition("job-1", JobState.RESUMING)
        store.transition("job-1", JobState.RUNNING)
        store.commit_chunk("job-1", 1, 25_000, 55_000, 25_000, 55_000)
        store.commit_chunk("job-1", 2, 50_000, 60_000, 55_000, 60_000)
        store.transition("job-1", JobState.COMPLETED)

        final = store.get_job("job-1")
        assert final is not None
        assert final.state is JobState.COMPLETED
        committed = store.list_committed_chunks("job-1")
        assert [c.chunk_index for c in committed] == [0, 1, 2]

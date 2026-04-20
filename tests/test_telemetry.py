"""
tests/test_telemetry.py
Tests for ai/telemetry.py -- local structured telemetry service.
"""
import json
import os
import threading
import time
import pytest

from ai.telemetry import TelemetryService


@pytest.fixture
def svc(tmp_path):
    """Create a TelemetryService with a temp database.

    Uses batch_size=1 so every record is committed immediately,
    preserving backward-compatible behavior for existing tests.
    """
    db_path = str(tmp_path / "test_telemetry.db")
    service = TelemetryService(db_path=db_path, enabled=True, batch_size=1)
    yield service
    service.close()


class TestTelemetryRecord:
    """Tests for recording and retrieving events."""

    def test_record_and_retrieve_events(self, svc):
        """Events recorded via record() appear in the database."""
        svc.record("test_event", {"key": "value"})
        svc.record("test_event", {"key": "value2"})

        summary = svc.get_summary(hours=1)
        assert summary["enabled"] is True
        assert summary["total_events"] == 2
        assert summary["by_type"]["test_event"] == 2

    def test_tool_call_records_correctly(self, svc):
        """tool_call() records structured tool call data."""
        svc.tool_call("execute_script", duration=0.5, success=True)

        # Verify via direct DB query
        cursor = svc._conn.execute(
            "SELECT data FROM events WHERE event_type = 'tool_call'"
        )
        row = cursor.fetchone()
        data = json.loads(row[0])

        assert data["name"] == "execute_script"
        assert data["duration_ms"] == 500.0
        assert data["success"] is True

    def test_api_call_records_correctly(self, svc):
        """api_call() records structured API call data."""
        svc.api_call(
            provider="anthropic", model="claude-sonnet-4-20250514",
            tokens_in=100, tokens_out=200, cost=0.003,
        )

        cursor = svc._conn.execute(
            "SELECT data FROM events WHERE event_type = 'api_call'"
        )
        row = cursor.fetchone()
        data = json.loads(row[0])

        assert data["provider"] == "anthropic"
        assert data["model"] == "claude-sonnet-4-20250514"
        assert data["tokens_in"] == 100
        assert data["tokens_out"] == 200
        assert data["cost"] == 0.003

    def test_condensation_records_correctly(self, svc):
        """condensation() records token reduction data."""
        svc.condensation(before_tokens=10000, after_tokens=3000)

        cursor = svc._conn.execute(
            "SELECT data FROM events WHERE event_type = 'condensation'"
        )
        row = cursor.fetchone()
        data = json.loads(row[0])

        assert data["before_tokens"] == 10000
        assert data["after_tokens"] == 3000
        assert data["reduction_pct"] == 70.0


class TestGetSummary:
    """Tests for the get_summary() method."""

    def test_returns_counts_by_type(self, svc):
        """get_summary() groups event counts by type."""
        svc.record("tool_call", {})
        svc.record("tool_call", {})
        svc.record("api_call", {})

        summary = svc.get_summary(hours=1)
        assert summary["by_type"]["tool_call"] == 2
        assert summary["by_type"]["api_call"] == 1
        assert summary["total_events"] == 3

    def test_respects_time_window(self, svc):
        """get_summary() only counts events within the time window."""
        # Insert an event with a very old timestamp
        svc._conn.execute(
            "INSERT INTO events (timestamp, event_type, data) VALUES (?, ?, ?)",
            (time.time() - 100000, "old_event", "{}"),
        )
        svc._conn.commit()

        # Insert a recent event
        svc.record("recent_event", {})

        summary = svc.get_summary(hours=1)
        assert summary["total_events"] == 1
        assert "old_event" not in summary["by_type"]
        assert summary["by_type"]["recent_event"] == 1


class TestDisabledService:
    """Tests for disabled telemetry."""

    def test_disabled_service_does_not_write(self, tmp_path):
        """A disabled service does not create a database or record events."""
        db_path = str(tmp_path / "disabled.db")
        svc = TelemetryService(db_path=db_path, enabled=False)

        svc.record("should_not_exist", {"key": "value"})
        svc.tool_call("test", 0.1, True)

        summary = svc.get_summary()
        assert summary == {"enabled": False}

        # No database file should be created
        assert not os.path.exists(db_path)
        svc.close()


class TestThreadSafety:
    """Tests for concurrent access."""

    def test_concurrent_writes(self, svc):
        """Multiple threads can write events without errors."""
        errors = []

        def writer(thread_id):
            try:
                for i in range(50):
                    svc.record("thread_event", {"thread": thread_id, "i": i})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == [], f"Thread errors: {errors}"

        summary = svc.get_summary(hours=1)
        assert summary["total_events"] == 200  # 4 threads x 50 events


class TestClose:
    """Tests for the close() method."""

    def test_close_works(self, tmp_path):
        """close() shuts down the database connection cleanly."""
        db_path = str(tmp_path / "close_test.db")
        svc = TelemetryService(db_path=db_path, enabled=True, batch_size=1)

        svc.record("test", {"a": 1})
        svc.close()

        assert svc._conn is None

        # Recording after close should not raise (TASK-191: graceful no-op)
        svc.record("should_be_ignored", {})

    def test_close_then_record_no_exception(self, tmp_path):
        """TASK-191: close() then record() must not raise AttributeError."""
        db_path = str(tmp_path / "race_test.db")
        svc = TelemetryService(db_path=db_path, enabled=True)
        svc.close()
        # This must be a no-op, not raise AttributeError on None.execute()
        svc.record("after_close", {"key": "value"})

    def test_close_commits_pending(self, tmp_path):
        """TASK-199: close() commits any pending (unbatched) records before closing."""
        db_path = str(tmp_path / "pending_close.db")
        svc = TelemetryService(db_path=db_path, enabled=True, batch_size=100)

        # Record fewer than batch_size events -- they remain uncommitted
        for i in range(5):
            svc.record("pending_event", {"i": i})
        assert svc._pending_count == 5

        svc.close()
        assert svc._conn is None

        # Re-open the DB and verify the records were persisted
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM events WHERE event_type = 'pending_event'")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 5


class TestBatching:
    """Tests for TASK-199: batched commit behavior."""

    def test_records_below_batch_size_not_committed(self, tmp_path):
        """Records below batch_size threshold don't trigger commit."""
        db_path = str(tmp_path / "batch_test.db")
        svc = TelemetryService(db_path=db_path, enabled=True, batch_size=10, flush_interval=9999)

        for i in range(5):
            svc.record("event", {"i": i})

        # pending_count should be 5 (no commit triggered)
        assert svc._pending_count == 5
        svc.close()

    def test_records_at_batch_size_trigger_commit(self, tmp_path):
        """Records at batch_size trigger commit and reset pending count."""
        db_path = str(tmp_path / "batch_trigger.db")
        svc = TelemetryService(db_path=db_path, enabled=True, batch_size=5, flush_interval=9999)

        for i in range(5):
            svc.record("event", {"i": i})

        # batch_size reached, so pending should reset to 0
        assert svc._pending_count == 0
        svc.close()

    def test_flush_forces_commit(self, tmp_path):
        """flush() commits pending records and resets count."""
        db_path = str(tmp_path / "flush_test.db")
        svc = TelemetryService(db_path=db_path, enabled=True, batch_size=100, flush_interval=9999)

        for i in range(3):
            svc.record("event", {"i": i})

        assert svc._pending_count == 3

        svc.flush()

        assert svc._pending_count == 0

        # Verify records are persisted
        summary = svc.get_summary(hours=1)
        assert summary["total_events"] == 3
        svc.close()

    def test_flush_on_empty_is_noop(self, tmp_path):
        """flush() with no pending records is a no-op."""
        db_path = str(tmp_path / "flush_empty.db")
        svc = TelemetryService(db_path=db_path, enabled=True, batch_size=100)

        svc.flush()  # should not raise
        assert svc._pending_count == 0
        svc.close()

"""
tests/test_message_sink.py
Unit tests for ai/message_sink.py -- message delivery abstraction.
"""

import json
import logging
import os

import pytest

from ai.message_sink import (
    FileSink,
    LoggingSink,
    MessageSink,
    MultiplexSink,
    NullSink,
    SocketIOSink,
)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestMessageSinkProtocol:
    """All concrete sinks must satisfy the MessageSink protocol."""

    def test_logging_sink_is_message_sink(self):
        assert isinstance(LoggingSink(), MessageSink)

    def test_null_sink_is_message_sink(self):
        assert isinstance(NullSink(), MessageSink)

    def test_file_sink_is_message_sink(self, tmp_path):
        sink = FileSink(str(tmp_path / "test.jsonl"))
        assert isinstance(sink, MessageSink)

    def test_socketio_sink_is_message_sink(self):
        sink = SocketIOSink(None)
        assert isinstance(sink, MessageSink)

    def test_multiplex_sink_is_message_sink(self):
        assert isinstance(MultiplexSink(), MessageSink)


# ---------------------------------------------------------------------------
# LoggingSink
# ---------------------------------------------------------------------------

class TestLoggingSink:
    """Validate LoggingSink logs events correctly."""

    def test_logs_event(self, caplog):
        sink = LoggingSink("test_sink")
        with caplog.at_level(logging.INFO, logger="test_sink"):
            sink.emit("test_event", {"key": "value"})
        assert "test_event" in caplog.text
        assert "value" in caplog.text

    def test_logs_truncated_data(self, caplog):
        sink = LoggingSink("test_sink")
        large_data = {"key": "x" * 1000}
        with caplog.at_level(logging.INFO, logger="test_sink"):
            sink.emit("big_event", large_data)
        # Data should be truncated to 500 chars
        assert "big_event" in caplog.text

    def test_handles_non_serializable_data(self, caplog):
        """Non-serializable data should not crash (uses default=str)."""
        sink = LoggingSink("test_sink")
        with caplog.at_level(logging.INFO, logger="test_sink"):
            sink.emit("obj_event", {"obj": object()})
        assert "obj_event" in caplog.text


# ---------------------------------------------------------------------------
# FileSink
# ---------------------------------------------------------------------------

class TestFileSink:
    """Validate FileSink writes JSONL entries."""

    def test_writes_jsonl_entry(self, tmp_path):
        path = str(tmp_path / "events.jsonl")
        sink = FileSink(path)
        sink.emit("test_event", {"key": "value"})

        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "test_event"
        assert entry["data"] == {"key": "value"}
        assert "timestamp" in entry

    def test_appends_multiple_entries(self, tmp_path):
        path = str(tmp_path / "events.jsonl")
        sink = FileSink(path)
        sink.emit("event_1", {"n": 1})
        sink.emit("event_2", {"n": 2})
        sink.emit("event_3", {"n": 3})

        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 3

    def test_creates_parent_directory(self, tmp_path):
        path = str(tmp_path / "subdir" / "deep" / "events.jsonl")
        sink = FileSink(path)
        sink.emit("test", {"ok": True})
        assert os.path.exists(path)

    def test_handles_write_error_gracefully(self, tmp_path):
        """Writing to an invalid path should not raise."""
        # Use a directory as the file path -- writing will fail
        dir_path = str(tmp_path / "a_directory")
        os.makedirs(dir_path)
        sink = FileSink(dir_path)
        # Should not raise
        sink.emit("fail_event", {"x": 1})


# ---------------------------------------------------------------------------
# NullSink
# ---------------------------------------------------------------------------

class TestNullSink:
    """Validate NullSink discards messages without error."""

    def test_does_not_crash(self):
        sink = NullSink()
        sink.emit("any_event", {"anything": True})
        sink.emit("another", {})
        # No assertion needed -- just verify no exception

    def test_handles_complex_data(self):
        sink = NullSink()
        sink.emit("complex", {"nested": {"deep": [1, 2, 3]}})


# ---------------------------------------------------------------------------
# SocketIOSink
# ---------------------------------------------------------------------------

class TestSocketIOSink:
    """Validate SocketIOSink handles various socketio states."""

    def test_handles_none_socketio_gracefully(self):
        """SocketIOSink with None instance should not crash on emit."""
        sink = SocketIOSink(None)
        sink.emit("test", {"data": 1})
        # No exception raised

    def test_calls_socketio_emit(self):
        """Verify it calls the underlying socketio.emit()."""

        class FakeSocketIO:
            def __init__(self):
                self.calls = []

            def emit(self, event, data):
                self.calls.append((event, data))

        sio = FakeSocketIO()
        sink = SocketIOSink(sio)
        sink.emit("my_event", {"key": "val"})
        assert len(sio.calls) == 1
        assert sio.calls[0] == ("my_event", {"key": "val"})

    def test_handles_socketio_error_gracefully(self):
        """Errors from socketio should be caught, not propagated."""

        class BrokenSocketIO:
            def emit(self, event, data):
                raise RuntimeError("connection lost")

        sink = SocketIOSink(BrokenSocketIO())
        # Should not raise
        sink.emit("test", {"x": 1})


# ---------------------------------------------------------------------------
# MultiplexSink
# ---------------------------------------------------------------------------

class TestMultiplexSink:
    """Validate MultiplexSink forwards to all sinks."""

    def test_forwards_to_all_sinks(self):
        calls = []

        class RecordingSink:
            def __init__(self, name):
                self.name = name

            def emit(self, event, data):
                calls.append((self.name, event, data))

        s1 = RecordingSink("a")
        s2 = RecordingSink("b")
        multi = MultiplexSink([s1, s2])
        multi.emit("test", {"v": 1})

        assert len(calls) == 2
        assert calls[0] == ("a", "test", {"v": 1})
        assert calls[1] == ("b", "test", {"v": 1})

    def test_add_sink(self):
        calls = []

        class RecordingSink:
            def emit(self, event, data):
                calls.append(event)

        multi = MultiplexSink()
        multi.emit("before_add", {})
        assert len(calls) == 0

        s = RecordingSink()
        multi.add(s)
        multi.emit("after_add", {})
        assert len(calls) == 1
        assert calls[0] == "after_add"

    def test_remove_sink(self):
        calls = []

        class RecordingSink:
            def __init__(self, name):
                self.name = name

            def emit(self, event, data):
                calls.append(self.name)

        s1 = RecordingSink("keep")
        s2 = RecordingSink("remove")
        multi = MultiplexSink([s1, s2])

        multi.remove(s2)
        multi.emit("test", {})

        assert calls == ["keep"]

    def test_handles_sink_error_gracefully(self):
        """One failing sink should not prevent others from receiving."""
        calls = []

        class GoodSink:
            def emit(self, event, data):
                calls.append("good")

        class BadSink:
            def emit(self, event, data):
                raise RuntimeError("broken")

        multi = MultiplexSink([BadSink(), GoodSink()])
        multi.emit("test", {})

        # The good sink should still have received the event
        assert "good" in calls

    def test_empty_multiplex_does_not_crash(self):
        multi = MultiplexSink()
        multi.emit("orphan", {"no": "sinks"})

    def test_remove_nonexistent_sink_is_safe(self):
        """Removing a sink that was never added should not raise."""

        class SomeSink:
            def emit(self, event, data):
                pass

        multi = MultiplexSink()
        multi.remove(SomeSink())  # Should not raise

    def test_concurrent_modification_during_emit(self):
        """TASK-205: Modifying _sinks from another thread during emit()
        must not raise RuntimeError (list changed size during iteration)."""
        import threading
        import time

        class SlowSink:
            """Sink that sleeps during emit to widen the race window."""
            def __init__(self):
                self.count = 0

            def emit(self, event, data):
                self.count += 1
                time.sleep(0.01)

        class FastSink:
            def __init__(self):
                self.count = 0

            def emit(self, event, data):
                self.count += 1

        slow = SlowSink()
        multi = MultiplexSink([slow])
        errors: list[Exception] = []

        def emitter():
            """Repeatedly emit events."""
            for _ in range(20):
                try:
                    multi.emit("tick", {"i": 1})
                except Exception as exc:
                    errors.append(exc)

        def mutator():
            """Repeatedly add/remove sinks while emitter runs."""
            for _ in range(20):
                fast = FastSink()
                try:
                    multi.add(fast)
                    time.sleep(0.005)
                    multi.remove(fast)
                except Exception as exc:
                    errors.append(exc)

        t1 = threading.Thread(target=emitter)
        t2 = threading.Thread(target=mutator)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Concurrent modification errors: {errors}"

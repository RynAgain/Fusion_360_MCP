"""
ai/message_sink.py
Message delivery abstraction for decoupling event emission.

Defines a Protocol for message sinks and provides concrete implementations
for Socket.IO, logging, and file-based delivery.
"""
import json
import logging
import os
import time
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class MessageSink(Protocol):
    """Protocol for message delivery targets."""

    def emit(self, event: str, data: dict[str, Any]) -> None:
        """Emit an event with data to the sink."""
        ...


class SocketIOSink:
    """Delivers messages via Socket.IO."""

    def __init__(self, socketio_instance):
        self._sio = socketio_instance

    def emit(self, event: str, data: dict[str, Any]) -> None:
        if self._sio:
            try:
                self._sio.emit(event, data)
            except Exception as exc:
                logger.warning("SocketIO emit failed: %s", exc)


class LoggingSink:
    """Logs messages via Python logging. Useful for testing."""

    def __init__(self, logger_name: str = "message_sink"):
        self._logger = logging.getLogger(logger_name)

    def emit(self, event: str, data: dict[str, Any]) -> None:
        self._logger.info(
            "Event: %s | Data: %s",
            event,
            json.dumps(data, default=str)[:500],
        )


class FileSink:
    """Writes messages to a JSONL file. Useful for debugging."""

    def __init__(self, file_path: str):
        self._path = file_path
        dir_name = os.path.dirname(file_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

    def emit(self, event: str, data: dict[str, Any]) -> None:
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                entry = {"timestamp": time.time(), "event": event, "data": data}
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            logger.warning("FileSink write failed: %s", exc)


class NullSink:
    """Discards all messages. Useful for testing."""

    def emit(self, event: str, data: dict[str, Any]) -> None:
        pass


class MultiplexSink:
    """Forwards messages to multiple sinks."""

    def __init__(self, sinks: list[MessageSink] | None = None):
        self._sinks: list[MessageSink] = list(sinks or [])

    def add(self, sink: MessageSink) -> None:
        """Add a sink to the multiplex."""
        self._sinks.append(sink)

    def remove(self, sink: MessageSink) -> None:
        """Remove a sink from the multiplex."""
        self._sinks = [s for s in self._sinks if s is not sink]

    def emit(self, event: str, data: dict[str, Any]) -> None:
        for sink in list(self._sinks):  # snapshot copy for thread safety
            try:
                sink.emit(event, data)
            except Exception as exc:
                logger.warning(
                    "MultiplexSink: sink %s failed: %s", type(sink).__name__, exc
                )

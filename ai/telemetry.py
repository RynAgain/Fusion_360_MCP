"""
ai/telemetry.py
Local-only structured telemetry for Artifex360.

Records structured events (tool calls, API calls, condensation, etc.)
to a local SQLite database for analytics. All data stays local.
This is opt-in and privacy-respecting.
"""
import json
import logging
import os
import sqlite3
import time
import threading
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "telemetry.db",
)


class TelemetryService:
    """Local telemetry service backed by SQLite.

    Records structured events for operational analytics.
    Thread-safe via a lock on the connection.
    """

    def __init__(self, db_path: str | None = None, enabled: bool = True,
                 batch_size: int = 50, flush_interval: float = 5.0):
        self._db_path = db_path or _DB_PATH
        self._enabled = enabled
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._pending_count = 0
        self._last_flush = time.monotonic()

        if self._enabled:
            self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite database and create tables."""
        try:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    data TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_type
                ON events(event_type)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_timestamp
                ON events(timestamp)
            """)
            self._conn.commit()
        except Exception as exc:
            logger.warning("Telemetry DB init failed: %s", exc)
            self._enabled = False

    def record(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Record a telemetry event.

        Args:
            event_type: Event category (e.g., "tool_call", "api_call")
            data: Event data dict
        """
        if not self._enabled:
            return

        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute(
                    "INSERT INTO events (timestamp, event_type, data) VALUES (?, ?, ?)",
                    (time.time(), event_type, json.dumps(data or {})),
                )
                self._pending_count += 1
                if (self._pending_count >= self._batch_size
                        or (time.monotonic() - self._last_flush) >= self._flush_interval):
                    self._conn.commit()
                    self._pending_count = 0
                    self._last_flush = time.monotonic()
            except Exception as exc:
                logger.debug("Telemetry record failed: %s", exc)

    def tool_call(self, name: str, duration: float, success: bool) -> None:
        """Record a tool call event."""
        self.record("tool_call", {
            "name": name, "duration_ms": round(duration * 1000, 1), "success": success,
        })

    def api_call(self, provider: str, model: str,
                 tokens_in: int, tokens_out: int, cost: float = 0.0) -> None:
        """Record an API call event."""
        self.record("api_call", {
            "provider": provider, "model": model,
            "tokens_in": tokens_in, "tokens_out": tokens_out, "cost": cost,
        })

    def condensation(self, before_tokens: int, after_tokens: int) -> None:
        """Record a context condensation event."""
        self.record("condensation", {
            "before_tokens": before_tokens, "after_tokens": after_tokens,
            "reduction_pct": round((1 - after_tokens / max(before_tokens, 1)) * 100, 1),
        })

    def get_summary(self, hours: float = 24) -> dict:
        """Get telemetry summary for the last N hours."""
        if not self._enabled or not self._conn:
            return {"enabled": False}

        cutoff = time.time() - (hours * 3600)

        with self._lock:
            try:
                cursor = self._conn.execute(
                    "SELECT event_type, COUNT(*) FROM events WHERE timestamp > ? GROUP BY event_type",
                    (cutoff,),
                )
                counts = dict(cursor.fetchall())

                cursor = self._conn.execute(
                    "SELECT COUNT(*) FROM events WHERE timestamp > ?", (cutoff,),
                )
                total = cursor.fetchone()[0]

                return {
                    "enabled": True,
                    "period_hours": hours,
                    "total_events": total,
                    "by_type": counts,
                }
            except Exception as exc:
                logger.debug("Telemetry summary failed: %s", exc)
                return {"enabled": True, "error": str(exc)}

    def flush(self) -> None:
        """Force commit any pending records."""
        with self._lock:
            if self._conn and self._pending_count > 0:
                self._conn.commit()
                self._pending_count = 0
                self._last_flush = time.monotonic()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn is not None:
                if self._pending_count > 0:
                    self._conn.commit()
                self._conn.close()
                self._conn = None

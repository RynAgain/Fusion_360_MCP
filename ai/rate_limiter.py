"""
ai/rate_limiter.py
Simple sliding-window rate limiter for Anthropic API calls.

Thread-safe -- all public methods acquire an internal lock before
touching shared state.
"""

import logging
import time
import threading

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Sliding-window rate limiter that caps the number of requests
    allowed within a rolling 60-second window.

    Usage::

        limiter = RateLimiter(max_requests_per_minute=10)
        if limiter.acquire():      # blocks until a slot is available
            make_api_call()
    """

    def __init__(self, max_requests_per_minute: int = 60):
        self.max_rpm: int = max(1, max_requests_per_minute)
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def update_limit(self, max_rpm: int) -> None:
        """Update the rate limit (clamped to >= 1)."""
        with self._lock:
            self.max_rpm = max(1, max_rpm)

    # ------------------------------------------------------------------
    # Acquire a request slot
    # ------------------------------------------------------------------

    def acquire(self, timeout: float = 60.0) -> bool:
        """
        Wait until a request slot is available within the sliding window.

        Returns:
            True  -- slot acquired, caller may proceed.
            False -- timed out waiting for a slot.
        """
        deadline = time.time() + timeout

        while time.time() < deadline:
            with self._lock:
                now = time.time()
                # Purge timestamps older than 60 s
                self._timestamps = [
                    t for t in self._timestamps if now - t < 60.0
                ]

                if len(self._timestamps) < self.max_rpm:
                    self._timestamps.append(now)
                    return True

            # Back off briefly before re-checking
            time.sleep(0.1)

        logger.warning(
            "Rate limiter: timed out waiting for request slot "
            "(limit: %d/min)", self.max_rpm,
        )
        return False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return current rate-limiting statistics."""
        with self._lock:
            now = time.time()
            self._timestamps = [
                t for t in self._timestamps if now - t < 60.0
            ]
            return {
                "requests_last_minute": len(self._timestamps),
                "max_requests_per_minute": self.max_rpm,
                "remaining": max(0, self.max_rpm - len(self._timestamps)),
            }

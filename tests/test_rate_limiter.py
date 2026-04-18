"""
tests/test_rate_limiter.py
Tests for ai/rate_limiter.py -- sliding-window rate limiter.
"""
import threading
import time

import pytest

from ai.rate_limiter import RateLimiter


class TestRateLimiterBasic:
    """Basic rate limiter functionality."""

    def test_init_with_defaults(self):
        rl = RateLimiter()
        assert rl is not None
        assert rl.max_rpm == 60

    def test_init_with_custom_rpm(self):
        rl = RateLimiter(max_requests_per_minute=10)
        assert rl.max_rpm == 10

    def test_init_clamps_to_one(self):
        rl = RateLimiter(max_requests_per_minute=0)
        assert rl.max_rpm == 1

    def test_acquire_under_limit(self):
        """Should acquire immediately when under the rate limit."""
        rl = RateLimiter(max_requests_per_minute=10)
        start = time.monotonic()
        result = rl.acquire()
        elapsed = time.monotonic() - start
        assert result is True
        assert elapsed < 1.0  # Should be near-instant

    def test_acquire_tracks_requests(self):
        """Multiple acquires should track request count."""
        rl = RateLimiter(max_requests_per_minute=5)
        for _ in range(5):
            assert rl.acquire(timeout=1.0) is True
        # All 5 slots used; next acquire should time out quickly
        assert rl.acquire(timeout=0.3) is False

    def test_update_limit(self):
        """update_limit should change the max_rpm."""
        rl = RateLimiter(max_requests_per_minute=5)
        rl.update_limit(20)
        assert rl.max_rpm == 20

    def test_update_limit_clamps_to_one(self):
        rl = RateLimiter(max_requests_per_minute=5)
        rl.update_limit(-10)
        assert rl.max_rpm == 1

    def test_get_stats(self):
        """get_stats should return correct structure and counts."""
        rl = RateLimiter(max_requests_per_minute=10)
        rl.acquire()
        rl.acquire()
        stats = rl.get_stats()
        assert stats["requests_last_minute"] == 2
        assert stats["max_requests_per_minute"] == 10
        assert stats["remaining"] == 8


class TestRateLimiterBlocking:
    """Tests for blocking/waiting behavior."""

    def test_acquire_returns_false_on_timeout(self):
        """Should return False when rate limit is reached and timeout expires."""
        rl = RateLimiter(max_requests_per_minute=1)
        assert rl.acquire(timeout=1.0) is True  # First should succeed
        start = time.monotonic()
        result = rl.acquire(timeout=0.5)
        elapsed = time.monotonic() - start
        assert result is False
        # Should have waited approximately the timeout duration
        assert elapsed >= 0.4  # Some tolerance

    def test_acquire_with_timeout_parameter(self):
        """Should respect the timeout parameter."""
        rl = RateLimiter(max_requests_per_minute=1)
        rl.acquire(timeout=1.0)
        start = time.monotonic()
        rl.acquire(timeout=0.2)
        elapsed = time.monotonic() - start
        assert elapsed < 0.5  # Should not wait longer than timeout


class TestRateLimiterThreadSafety:
    """Thread safety tests."""

    def test_concurrent_acquires(self):
        """Multiple threads acquiring should not corrupt state."""
        rl = RateLimiter(max_requests_per_minute=100)
        errors = []

        def worker():
            try:
                for _ in range(10):
                    rl.acquire(timeout=5.0)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Errors in threads: {errors}"

        # Total successful acquires should equal 100 (10 threads x 10 each)
        stats = rl.get_stats()
        assert stats["requests_last_minute"] == 100

    def test_concurrent_acquire_and_stats(self):
        """Calling get_stats while acquiring should not deadlock or crash."""
        rl = RateLimiter(max_requests_per_minute=50)
        errors = []

        def acquire_worker():
            try:
                for _ in range(10):
                    rl.acquire(timeout=5.0)
            except Exception as exc:
                errors.append(exc)

        def stats_worker():
            try:
                for _ in range(10):
                    rl.get_stats()
            except Exception as exc:
                errors.append(exc)

        threads = (
            [threading.Thread(target=acquire_worker) for _ in range(5)]
            + [threading.Thread(target=stats_worker) for _ in range(3)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Errors in threads: {errors}"

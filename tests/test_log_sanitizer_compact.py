"""
tests/test_log_sanitizer_compact.py
Tests for the compact_log function in ai/log_sanitizer.py.
"""
import pytest

from ai.log_sanitizer import compact_log


class TestCompactLog:
    """Validate log compaction behaviour."""

    def test_removes_consecutive_duplicates(self):
        """Consecutive duplicate lines are collapsed to one."""
        log = "line A\nline A\nline A\nline B\nline B\nline C"
        result = compact_log(log, max_lines=100)
        assert result == "line A\nline B\nline C"

    def test_preserves_error_lines(self):
        """ERROR lines from the beginning are preserved even after truncation."""
        lines = ["ERROR: something broke at start"]
        lines += [f"info line {i}" for i in range(100)]
        log = "\n".join(lines)
        result = compact_log(log, max_lines=10)
        assert "ERROR: something broke at start" in result

    def test_preserves_warning_lines(self):
        """WARNING lines from the beginning are preserved even after truncation."""
        lines = ["WARNING: disk almost full"]
        lines += [f"debug line {i}" for i in range(100)]
        log = "\n".join(lines)
        result = compact_log(log, max_lines=10)
        assert "WARNING: disk almost full" in result

    def test_keeps_last_n_lines(self):
        """When log exceeds max_lines, the tail is kept."""
        lines = [f"line {i}" for i in range(200)]
        log = "\n".join(lines)
        result = compact_log(log, max_lines=50)
        result_lines = result.splitlines()
        # Should end with the last line
        assert result_lines[-1] == "line 199"
        # Should contain "line 150" (200-50=150)
        assert "line 150" in result

    def test_short_log_unchanged(self):
        """Logs under max_lines are returned as-is (after dedup)."""
        log = "alpha\nbeta\ngamma"
        assert compact_log(log, max_lines=50) == log

    def test_empty_log(self):
        """Empty string returns empty string."""
        assert compact_log("") == ""

    def test_none_returns_none(self):
        """None input returns None (falsy passthrough)."""
        assert compact_log(None) is None

    def test_error_in_tail_not_duplicated(self):
        """ERROR lines already in the tail are not duplicated."""
        lines = [f"info line {i}" for i in range(20)]
        lines.append("ERROR: recent error")
        log = "\n".join(lines)
        result = compact_log(log, max_lines=50)
        # Only one occurrence of the error line
        assert result.count("ERROR: recent error") == 1

    def test_case_insensitive_error_detection(self):
        """Error detection is case-insensitive."""
        lines = ["error: lowercase problem"]
        lines += [f"line {i}" for i in range(100)]
        log = "\n".join(lines)
        result = compact_log(log, max_lines=10)
        assert "error: lowercase problem" in result

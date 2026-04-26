"""
tests/test_rebuild_loop_detector.py
TASK-230: Unit tests for RebuildLoopDetector -- new_document call counting,
warning thresholds, error summary integration, and reset.
"""
import pytest

from ai.repetition_detector import (
    RebuildLoopDetector,
    ScriptErrorTracker,
    REBUILD_WARN_THRESHOLD,
    REBUILD_CRITICAL_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_error_tracker_with_errors() -> ScriptErrorTracker:
    """Return a ScriptErrorTracker pre-loaded with two distinct errors."""
    tracker = ScriptErrorTracker()
    # Record two different error signatures
    tracker.record_error({
        "error_details": {
            "script_error": {
                "error_type": "AttributeError",
                "error_message": "'BRepBody' object has no attribute 'areaProperties'",
            },
        },
    })
    tracker.record_error({
        "error_details": {
            "script_error": {
                "error_type": "TypeError",
                "error_message": "expected str, got NoneType",
            },
        },
    })
    return tracker


# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------

class TestDefaultThresholds:

    def test_warn_threshold_default(self):
        assert REBUILD_WARN_THRESHOLD == 2

    def test_critical_threshold_default(self):
        assert REBUILD_CRITICAL_THRESHOLD == 3


# ---------------------------------------------------------------------------
# Counting and thresholds
# ---------------------------------------------------------------------------

class TestCounting:

    def test_first_call_no_warning(self):
        det = RebuildLoopDetector()
        result = det.record_new_document()
        assert result is None
        assert det.count == 1

    def test_second_call_triggers_warning(self):
        det = RebuildLoopDetector(warn_threshold=2, critical_threshold=3)
        det.record_new_document()
        result = det.record_new_document()
        assert result is not None
        assert "[WARNING]" in result
        assert "2 times" in result
        assert det.count == 2

    def test_third_call_triggers_critical(self):
        det = RebuildLoopDetector(warn_threshold=2, critical_threshold=3)
        det.record_new_document()
        det.record_new_document()
        result = det.record_new_document()
        assert result is not None
        assert "[CRITICAL]" in result
        assert "3 design restarts" in result
        assert det.count == 3

    def test_fourth_call_still_critical(self):
        det = RebuildLoopDetector(warn_threshold=2, critical_threshold=3)
        for _ in range(3):
            det.record_new_document()
        result = det.record_new_document()
        assert "[CRITICAL]" in result
        assert "4 design restarts" in result

    def test_custom_thresholds(self):
        det = RebuildLoopDetector(warn_threshold=5, critical_threshold=10)
        for _ in range(4):
            result = det.record_new_document()
        assert result is None  # 4 < 5

        result = det.record_new_document()
        assert "[WARNING]" in result  # 5 >= 5

        for _ in range(4):
            result = det.record_new_document()
        assert "[WARNING]" in result  # 9 < 10

        result = det.record_new_document()
        assert "[CRITICAL]" in result  # 10 >= 10


# ---------------------------------------------------------------------------
# Error summary integration
# ---------------------------------------------------------------------------

class TestErrorSummaryIntegration:

    def test_warning_includes_error_summary(self):
        det = RebuildLoopDetector(warn_threshold=2)
        tracker = _make_error_tracker_with_errors()
        det.record_new_document(tracker)
        result = det.record_new_document(tracker)
        assert "AttributeError" in result
        assert "TypeError" in result

    def test_critical_includes_error_summary(self):
        det = RebuildLoopDetector(warn_threshold=2, critical_threshold=3)
        tracker = _make_error_tracker_with_errors()
        det.record_new_document(tracker)
        det.record_new_document(tracker)
        result = det.record_new_document(tracker)
        assert "[CRITICAL]" in result
        assert "AttributeError" in result

    def test_warning_without_tracker(self):
        det = RebuildLoopDetector(warn_threshold=2)
        det.record_new_document()
        result = det.record_new_document()
        assert "[WARNING]" in result
        assert "unknown" in result  # No tracker -> "unknown" errors

    def test_warning_with_empty_tracker(self):
        det = RebuildLoopDetector(warn_threshold=2)
        tracker = ScriptErrorTracker()  # No errors recorded
        det.record_new_document(tracker)
        result = det.record_new_document(tracker)
        assert "[WARNING]" in result
        # Empty tracker -> empty error summary -> "unknown" fallback
        assert "unknown" in result.lower() or "errors:" in result.lower()

    def test_critical_without_tracker_mentions_user(self):
        det = RebuildLoopDetector(warn_threshold=2, critical_threshold=3)
        for _ in range(3):
            result = det.record_new_document()
        assert "asking the user" in result.lower() or "user" in result.lower()

    def test_critical_with_tracker_mentions_user(self):
        det = RebuildLoopDetector(warn_threshold=2, critical_threshold=3)
        tracker = _make_error_tracker_with_errors()
        for _ in range(3):
            result = det.record_new_document(tracker)
        assert "user" in result.lower()


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:

    def test_reset_clears_count(self):
        det = RebuildLoopDetector()
        det.record_new_document()
        det.record_new_document()
        assert det.count == 2
        det.reset()
        assert det.count == 0

    def test_after_reset_no_warning(self):
        det = RebuildLoopDetector(warn_threshold=2)
        det.record_new_document()
        det.record_new_document()
        det.reset()
        result = det.record_new_document()
        assert result is None


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:

    def test_stats_structure(self):
        det = RebuildLoopDetector()
        stats = det.get_stats()
        assert "new_document_count" in stats
        assert "warn_threshold" in stats
        assert "critical_threshold" in stats

    def test_stats_reflect_count(self):
        det = RebuildLoopDetector()
        det.record_new_document()
        det.record_new_document()
        stats = det.get_stats()
        assert stats["new_document_count"] == 2

    def test_stats_reflect_thresholds(self):
        det = RebuildLoopDetector(warn_threshold=5, critical_threshold=8)
        stats = det.get_stats()
        assert stats["warn_threshold"] == 5
        assert stats["critical_threshold"] == 8


# ---------------------------------------------------------------------------
# _get_error_summary helper
# ---------------------------------------------------------------------------

class TestGetErrorSummary:

    def test_with_none_tracker(self):
        result = RebuildLoopDetector._get_error_summary(None)
        assert result == ""

    def test_with_empty_tracker(self):
        tracker = ScriptErrorTracker()
        result = RebuildLoopDetector._get_error_summary(tracker)
        assert result == ""

    def test_with_populated_tracker(self):
        tracker = _make_error_tracker_with_errors()
        result = RebuildLoopDetector._get_error_summary(tracker)
        assert "AttributeError" in result
        assert "TypeError" in result

    def test_limits_to_five_entries(self):
        tracker = ScriptErrorTracker()
        for i in range(10):
            tracker.record_error({
                "error_details": {
                    "script_error": {
                        "error_type": f"Error{i}",
                        "error_message": f"msg{i}",
                    },
                },
            })
        result = RebuildLoopDetector._get_error_summary(tracker)
        # Should only include up to 5 entries
        count = result.count(",")
        assert count <= 4  # N entries -> N-1 commas max


# ---------------------------------------------------------------------------
# count property
# ---------------------------------------------------------------------------

class TestCountProperty:

    def test_count_starts_at_zero(self):
        det = RebuildLoopDetector()
        assert det.count == 0

    def test_count_increments(self):
        det = RebuildLoopDetector()
        det.record_new_document()
        assert det.count == 1
        det.record_new_document()
        assert det.count == 2

    def test_count_resets(self):
        det = RebuildLoopDetector()
        det.record_new_document()
        det.reset()
        assert det.count == 0


# ---------------------------------------------------------------------------
# Integration scenario: simulate convo_425 rebuild loop
# ---------------------------------------------------------------------------

class TestConvo425Scenario:
    """Simulate the exact pattern from convo_425 where new_document
    was called 3 times with the same script errors each rebuild."""

    def test_three_rebuilds_with_same_errors(self):
        det = RebuildLoopDetector(warn_threshold=2, critical_threshold=3)
        tracker = ScriptErrorTracker(warn_threshold=2, block_threshold=3)

        # Simulate errors from first rebuild
        for _ in range(5):
            tracker.record_error({
                "error_details": {
                    "script_error": {
                        "error_type": "AttributeError",
                        "error_message": "'BRepBody' object has no attribute 'areaProperties'",
                    },
                },
            })

        # First new_document -- no warning
        result1 = det.record_new_document(tracker)
        assert result1 is None

        # Second new_document -- WARNING
        result2 = det.record_new_document(tracker)
        assert "[WARNING]" in result2
        assert "areaProperties" in result2

        # Third new_document -- CRITICAL
        result3 = det.record_new_document(tracker)
        assert "[CRITICAL]" in result3
        assert "areaProperties" in result3
        assert "user" in result3.lower()

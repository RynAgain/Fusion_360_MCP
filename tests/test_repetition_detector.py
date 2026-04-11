"""
tests/test_repetition_detector.py
Unit tests for ai.repetition_detector -- identical call detection,
similar call detection, reset, and statistics.
"""
import pytest

from ai.repetition_detector import RepetitionDetector


# ---------------------------------------------------------------------------
# No repetition
# ---------------------------------------------------------------------------

class TestNoRepetition:

    def test_different_tools_no_flag(self):
        det = RepetitionDetector()
        r1 = det.record("create_box", {"width": 1})
        r2 = det.record("create_cylinder", {"radius": 2})
        r3 = det.record("add_fillet", {"radius": 0.1})
        assert not r1["repeated"]
        assert not r2["repeated"]
        assert not r3["repeated"]

    def test_single_call_no_flag(self):
        det = RepetitionDetector()
        result = det.record("create_box", {"width": 5})
        assert result["repeated"] is False
        assert result["type"] is None
        assert result["count"] == 0
        assert result["message"] is None


# ---------------------------------------------------------------------------
# Identical calls
# ---------------------------------------------------------------------------

class TestIdenticalCalls:

    def test_identical_calls_trigger(self):
        det = RepetitionDetector(max_identical=3)
        args = {"radius": 5, "height": 10}
        det.record("add_fillet", args)
        det.record("add_fillet", args)
        result = det.record("add_fillet", args)
        assert result["repeated"] is True
        assert result["type"] == "identical"
        assert result["count"] == 3
        assert "add_fillet" in result["message"]

    def test_below_identical_threshold(self):
        det = RepetitionDetector(max_identical=3)
        args = {"radius": 5}
        det.record("add_fillet", args)
        result = det.record("add_fillet", args)
        assert result["repeated"] is False

    def test_different_args_not_identical(self):
        det = RepetitionDetector(max_identical=3)
        det.record("add_fillet", {"radius": 1})
        det.record("add_fillet", {"radius": 2})
        result = det.record("add_fillet", {"radius": 3})
        # Different args each time -- identical check should not trigger
        assert result["type"] != "identical"

    def test_interrupted_sequence_resets_identical_count(self):
        det = RepetitionDetector(max_identical=3)
        args = {"radius": 5}
        det.record("add_fillet", args)
        det.record("add_fillet", args)
        # Interrupt with a different tool
        det.record("take_screenshot", {})
        # Restart the sequence -- count should be 1, not 3
        result = det.record("add_fillet", args)
        assert result["repeated"] is False


# ---------------------------------------------------------------------------
# Similar calls
# ---------------------------------------------------------------------------

class TestSimilarCalls:

    def test_similar_calls_trigger(self):
        det = RepetitionDetector(max_similar=5)
        for i in range(4):
            det.record("add_fillet", {"radius": i})
        result = det.record("add_fillet", {"radius": 99})
        assert result["repeated"] is True
        assert result["type"] == "similar"
        assert result["count"] == 5
        assert "add_fillet" in result["message"]

    def test_below_similar_threshold(self):
        det = RepetitionDetector(max_similar=5)
        for i in range(3):
            det.record("add_fillet", {"radius": i})
        result = det.record("add_fillet", {"radius": 99})
        assert result["repeated"] is False

    def test_mixed_tools_only_count_target(self):
        det = RepetitionDetector(max_similar=5)
        det.record("create_box", {"w": 1})
        det.record("add_fillet", {"radius": 1})
        det.record("create_box", {"w": 2})
        det.record("add_fillet", {"radius": 2})
        det.record("create_box", {"w": 3})
        # add_fillet only called 2 times -- should not trigger
        result = det.record("add_fillet", {"radius": 3})
        assert result["repeated"] is False


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:

    def test_reset_clears_history(self):
        det = RepetitionDetector(max_identical=3)
        args = {"radius": 5}
        det.record("add_fillet", args)
        det.record("add_fillet", args)
        det.reset()
        # After reset, the sequence counter starts fresh
        result = det.record("add_fillet", args)
        assert result["repeated"] is False
        assert det.get_stats()["history_length"] == 1

    def test_reset_allows_fresh_start(self):
        det = RepetitionDetector(max_similar=3)
        for i in range(5):
            det.record("create_box", {"w": i})
        det.reset()
        result = det.record("create_box", {"w": 0})
        assert result["repeated"] is False


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:

    def test_returns_expected_keys(self):
        det = RepetitionDetector()
        stats = det.get_stats()
        assert "history_length" in stats
        assert "tool_counts" in stats

    def test_tool_counts_accurate(self):
        det = RepetitionDetector()
        det.record("a", {})
        det.record("b", {})
        det.record("a", {"x": 1})
        stats = det.get_stats()
        assert stats["history_length"] == 3
        assert stats["tool_counts"]["a"] == 2
        assert stats["tool_counts"]["b"] == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_arguments(self):
        det = RepetitionDetector(max_identical=2)
        det.record("take_screenshot", {})
        result = det.record("take_screenshot", {})
        assert result["repeated"] is True
        assert result["type"] == "identical"

    def test_window_size_pruning(self):
        """History should not grow beyond WINDOW_SIZE."""
        det = RepetitionDetector()
        for i in range(50):
            det.record(f"tool_{i}", {"i": i})
        assert det.get_stats()["history_length"] <= 10

    def test_hash_stability(self):
        """Same args in different key order should produce the same hash."""
        det = RepetitionDetector()
        h1 = det._hash_args({"a": 1, "b": 2})
        h2 = det._hash_args({"b": 2, "a": 1})
        assert h1 == h2

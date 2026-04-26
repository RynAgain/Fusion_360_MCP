"""
tests/test_progress_tracker.py
TASK-234: Tests for ai/progress_tracker.py -- ProgressTracker.

Covers tool classification, counter tracking, ratio calculation,
warning threshold triggering, and reset functionality.
"""

import pytest

from ai.progress_tracker import (
    ProgressTracker,
    PRODUCTIVE_TOOLS,
    THRASHING_TOOLS,
    RESTART_TOOLS,
)


# ---------------------------------------------------------------------------
# Tool classification tests
# ---------------------------------------------------------------------------

class TestClassify:
    """Test: ProgressTracker.classify() categorises tools correctly."""

    @pytest.mark.parametrize("tool_name", sorted(PRODUCTIVE_TOOLS))
    def test_productive_tools(self, tool_name):
        assert ProgressTracker.classify(tool_name) == "productive"

    @pytest.mark.parametrize("tool_name", sorted(THRASHING_TOOLS))
    def test_thrashing_tools(self, tool_name):
        assert ProgressTracker.classify(tool_name) == "thrashing"

    @pytest.mark.parametrize("tool_name", sorted(RESTART_TOOLS))
    def test_restart_tools(self, tool_name):
        assert ProgressTracker.classify(tool_name) == "restart"

    def test_execute_script_success_is_productive(self):
        result = {"success": True, "stdout": "ok"}
        assert ProgressTracker.classify("execute_script", result) == "productive"

    def test_execute_script_failure_is_thrashing(self):
        result = {"success": False, "error": "AttributeError: ..."}
        assert ProgressTracker.classify("execute_script", result) == "thrashing"

    def test_execute_script_no_result_is_productive(self):
        """With no result dict, execute_script defaults to productive."""
        assert ProgressTracker.classify("execute_script") == "productive"

    def test_execute_script_no_success_key_defaults_productive(self):
        """If 'success' key is missing, default to True -> productive."""
        result = {"stdout": "hello"}
        assert ProgressTracker.classify("execute_script", result) == "productive"

    def test_neutral_tools(self):
        """get_*, take_screenshot, validate_design are neutral."""
        for tool in ["get_body_list", "get_timeline", "take_screenshot",
                      "validate_design", "get_body_properties",
                      "get_sketch_info"]:
            assert ProgressTracker.classify(tool) == "neutral", (
                f"Expected '{tool}' to be neutral"
            )

    def test_unknown_tool_is_neutral(self):
        assert ProgressTracker.classify("some_unknown_tool") == "neutral"


# ---------------------------------------------------------------------------
# Counter tracking tests
# ---------------------------------------------------------------------------

class TestCounterTracking:
    """Test: Counters increment correctly."""

    def test_productive_count(self):
        t = ProgressTracker()
        t.record("create_box", {"success": True})
        t.record("extrude", {"success": True})
        assert t.productive_count == 2

    def test_thrashing_count(self):
        t = ProgressTracker()
        t.record("undo", {"success": True})
        t.record("delete_body", {"success": True})
        assert t.thrashing_count == 2

    def test_neutral_count(self):
        t = ProgressTracker()
        t.record("get_body_list", {"success": True})
        t.record("take_screenshot", {"success": True})
        assert t.neutral_count == 2

    def test_restart_count(self):
        t = ProgressTracker()
        t.record("new_document", {"success": True})
        assert t.restart_count == 1
        # Restarts also count as thrashing
        assert t.thrashing_count == 1

    def test_total_calls_excludes_nothing(self):
        """total_calls = productive + thrashing + neutral (restarts included in thrashing)."""
        t = ProgressTracker()
        t.record("create_box", {"success": True})         # productive
        t.record("undo", {"success": True})                # thrashing
        t.record("get_body_list", {"success": True})       # neutral
        t.record("new_document", {"success": True})        # restart (+thrashing)
        # productive=1, thrashing=2 (undo+restart), neutral=1 => total=4
        assert t.total_calls == 4

    def test_execute_script_failure_counted_as_thrashing(self):
        t = ProgressTracker()
        t.record("execute_script", {"success": False, "error": "err"})
        assert t.thrashing_count == 1
        assert t.productive_count == 0

    def test_execute_script_success_counted_as_productive(self):
        t = ProgressTracker()
        t.record("execute_script", {"success": True})
        assert t.productive_count == 1
        assert t.thrashing_count == 0


# ---------------------------------------------------------------------------
# Ratio calculation tests
# ---------------------------------------------------------------------------

class TestRatioCalculation:
    """Test: thrashing_ratio edge cases."""

    def test_ratio_zero_with_no_calls(self):
        t = ProgressTracker()
        assert t.thrashing_ratio == 0.0

    def test_ratio_zero_all_productive(self):
        t = ProgressTracker()
        t.record("create_box", {"success": True})
        t.record("extrude", {"success": True})
        assert t.thrashing_ratio == 0.0

    def test_ratio_one_all_thrashing(self):
        t = ProgressTracker()
        t.record("undo", {"success": True})
        t.record("delete_body", {"success": True})
        assert t.thrashing_ratio == 1.0

    def test_ratio_half_mixed(self):
        t = ProgressTracker()
        t.record("create_box", {"success": True})
        t.record("undo", {"success": True})
        assert t.thrashing_ratio == pytest.approx(0.5)

    def test_ratio_ignores_neutral_in_denominator(self):
        """Neutral calls do NOT affect the thrashing ratio denominator."""
        t = ProgressTracker()
        t.record("create_box", {"success": True})         # productive
        t.record("undo", {"success": True})                # thrashing
        t.record("get_body_list", {"success": True})       # neutral
        # ratio = 1 / (1 + 1) = 0.5  (neutral ignored)
        assert t.thrashing_ratio == pytest.approx(0.5)

    def test_ratio_only_neutral_is_zero(self):
        """If only neutral calls exist, ratio is 0 (no div-by-zero)."""
        t = ProgressTracker()
        t.record("get_body_list")
        t.record("take_screenshot")
        assert t.thrashing_ratio == 0.0


# ---------------------------------------------------------------------------
# Warning threshold tests
# ---------------------------------------------------------------------------

class TestWarningThreshold:
    """Test: Warning fires at the right time."""

    def test_no_warning_below_min_calls(self):
        """No warning when total_calls < min_calls_for_warning."""
        t = ProgressTracker(min_calls_for_warning=10)
        # 9 thrashing calls -- below threshold count
        for _ in range(9):
            warning = t.record("undo")
        assert warning is None

    def test_warning_at_threshold(self):
        """Warning fires when ratio > 0.6 AND total_calls >= 10."""
        t = ProgressTracker(thrashing_ratio_threshold=0.6, min_calls_for_warning=10)
        # 3 productive, 7 thrashing, 2 neutral = 12 total, ratio = 7/10 = 0.7
        for _ in range(3):
            t.record("create_box", {"success": True})
        for _ in range(2):
            t.record("get_body_list")
        warning = None
        for _ in range(7):
            w = t.record("undo")
            if w:
                warning = w
        assert warning is not None
        assert "[THRASHING WARNING]" in warning

    def test_warning_only_once(self):
        """Warning is emitted only on first threshold breach."""
        t = ProgressTracker(thrashing_ratio_threshold=0.5, min_calls_for_warning=5)
        warnings = []
        for _ in range(10):
            w = t.record("undo")
            if w:
                warnings.append(w)
        assert len(warnings) == 1

    def test_no_warning_below_ratio(self):
        """No warning when ratio <= threshold even with enough calls."""
        t = ProgressTracker(thrashing_ratio_threshold=0.6, min_calls_for_warning=5)
        # 5 productive, 2 thrashing, 3 neutral = 10 total, ratio = 2/7 ≈ 0.29
        for _ in range(5):
            t.record("create_box", {"success": True})
        for _ in range(3):
            t.record("get_body_list")
        for _ in range(2):
            w = t.record("undo")
        assert w is None

    def test_warning_includes_counts(self):
        """Warning message includes productive and thrashing counts."""
        t = ProgressTracker(thrashing_ratio_threshold=0.5, min_calls_for_warning=5)
        for _ in range(2):
            t.record("create_box", {"success": True})
        warning = None
        for _ in range(8):
            w = t.record("undo")
            if w:
                warning = w
        assert warning is not None
        assert "2/" in warning  # productive count
        # Warning fires at call 5 (2 productive + 3 thrashing), ratio=3/5=0.6>0.5
        assert "3 calls" in warning  # thrashing count at time of warning


# ---------------------------------------------------------------------------
# Reset tests
# ---------------------------------------------------------------------------

class TestReset:
    """Test: reset() clears all state."""

    def test_reset_clears_all_counters(self):
        t = ProgressTracker()
        t.record("create_box", {"success": True})
        t.record("undo")
        t.record("get_body_list")
        t.record("new_document")
        t.reset()
        assert t.productive_count == 0
        assert t.thrashing_count == 0
        assert t.neutral_count == 0
        assert t.restart_count == 0
        assert t.total_calls == 0

    def test_reset_allows_new_warning(self):
        """After reset, warning can fire again."""
        t = ProgressTracker(thrashing_ratio_threshold=0.5, min_calls_for_warning=5)
        # Trigger warning
        for _ in range(6):
            t.record("undo")
        t.reset()
        # Trigger again
        warnings = []
        for _ in range(6):
            w = t.record("undo")
            if w:
                warnings.append(w)
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# to_dict tests
# ---------------------------------------------------------------------------

class TestToDict:
    """Test: to_dict() returns correct snapshot."""

    def test_empty_state(self):
        t = ProgressTracker()
        d = t.to_dict()
        assert d["productive_count"] == 0
        assert d["thrashing_count"] == 0
        assert d["neutral_count"] == 0
        assert d["restart_count"] == 0
        assert d["total_calls"] == 0
        assert d["thrashing_ratio"] == 0.0

    def test_populated_state(self):
        t = ProgressTracker()
        t.record("create_box", {"success": True})
        t.record("undo")
        t.record("get_body_list")
        t.record("new_document")
        d = t.to_dict()
        assert d["productive_count"] == 1
        assert d["thrashing_count"] == 2  # undo + restart
        assert d["neutral_count"] == 1
        assert d["restart_count"] == 1
        assert d["total_calls"] == 4
        assert d["thrashing_ratio"] == pytest.approx(2 / 3)

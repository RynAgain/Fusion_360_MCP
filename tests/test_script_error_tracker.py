"""
tests/test_script_error_tracker.py
TASK-227: Unit tests for ScriptErrorTracker -- script error signature
tracking, repeat counting, correction hints, escalation, and reset.
"""
import pytest

from ai.repetition_detector import (
    ScriptErrorTracker,
    KNOWN_SCRIPT_ERROR_CORRECTIONS,
    SCRIPT_ERROR_WARN_THRESHOLD,
    SCRIPT_ERROR_BLOCK_THRESHOLD,
    _lookup_known_correction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(error_type: str, error_message: str, line_number: int = 4) -> dict:
    """Build a minimal enriched tool result with script_error info."""
    return {
        "success": False,
        "error": f"{error_type}: {error_message}",
        "error_type": "SCRIPT_ERROR",
        "error_details": {
            "script_error": {
                "line_number": line_number,
                "error_type": error_type,
                "error_message": error_message,
            },
            "suggestion": "Parse the traceback to identify the error line.",
        },
        "stderr": f"Traceback ...\n  line {line_number}\n{error_type}: {error_message}",
    }


# ---------------------------------------------------------------------------
# Signature extraction
# ---------------------------------------------------------------------------

class TestExtractSignature:

    def test_extracts_from_valid_result(self):
        result = _make_result("AttributeError", "'BRepBody' object has no attribute 'areaProperties'")
        sig = ScriptErrorTracker.extract_signature(result)
        assert sig == ("AttributeError", "'BRepBody' object has no attribute 'areaProperties'")

    def test_returns_none_for_non_dict(self):
        assert ScriptErrorTracker.extract_signature("not a dict") is None

    def test_returns_none_for_missing_error_details(self):
        assert ScriptErrorTracker.extract_signature({"success": False}) is None

    def test_returns_none_for_missing_script_error(self):
        result = {"error_details": {"suggestion": "something"}}
        assert ScriptErrorTracker.extract_signature(result) is None

    def test_returns_none_for_empty_error_type(self):
        result = {
            "error_details": {
                "script_error": {
                    "error_type": "",
                    "error_message": "some message",
                }
            }
        }
        assert ScriptErrorTracker.extract_signature(result) is None

    def test_returns_none_for_empty_error_message(self):
        result = {
            "error_details": {
                "script_error": {
                    "error_type": "AttributeError",
                    "error_message": "",
                }
            }
        }
        assert ScriptErrorTracker.extract_signature(result) is None

    def test_strips_whitespace(self):
        result = {
            "error_details": {
                "script_error": {
                    "error_type": "  TypeError  ",
                    "error_message": "  msg  ",
                }
            }
        }
        sig = ScriptErrorTracker.extract_signature(result)
        assert sig == ("TypeError", "msg")


# ---------------------------------------------------------------------------
# Repeat counting
# ---------------------------------------------------------------------------

class TestRepeatCounting:

    def test_first_occurrence_not_repeated(self):
        tracker = ScriptErrorTracker()
        result = _make_result("AttributeError", "some error")
        info = tracker.record_error(result)
        assert info["repeated"] is False
        assert info["count"] == 1
        assert info["message"] is None

    def test_second_occurrence_triggers_warning(self):
        tracker = ScriptErrorTracker(warn_threshold=2)
        result = _make_result("AttributeError", "some error")
        tracker.record_error(result)
        info = tracker.record_error(result)
        assert info["repeated"] is True
        assert info["count"] == 2
        assert "SCRIPT ERROR REPEATED 2x" in info["message"]

    def test_third_occurrence_triggers_blocked(self):
        tracker = ScriptErrorTracker(warn_threshold=2, block_threshold=3)
        result = _make_result("AttributeError", "some error")
        tracker.record_error(result)
        tracker.record_error(result)
        info = tracker.record_error(result)
        assert info["repeated"] is True
        assert info["blocked"] is True
        assert info["count"] == 3
        assert "BLOCKED" in info["message"]

    def test_count_increments_beyond_block(self):
        tracker = ScriptErrorTracker(warn_threshold=2, block_threshold=3)
        result = _make_result("TypeError", "test")
        for _ in range(5):
            info = tracker.record_error(result)
        assert info["count"] == 5
        assert info["blocked"] is True


# ---------------------------------------------------------------------------
# Different signatures tracked independently
# ---------------------------------------------------------------------------

class TestIndependentSignatures:

    def test_different_errors_counted_separately(self):
        tracker = ScriptErrorTracker(warn_threshold=2)
        err_a = _make_result("AttributeError", "error A")
        err_b = _make_result("TypeError", "error B")

        tracker.record_error(err_a)
        info_b = tracker.record_error(err_b)
        assert info_b["repeated"] is False  # first occurrence of B
        assert info_b["count"] == 1

        info_a2 = tracker.record_error(err_a)
        assert info_a2["repeated"] is True  # second occurrence of A
        assert info_a2["count"] == 2

    def test_same_type_different_message(self):
        tracker = ScriptErrorTracker(warn_threshold=2)
        err1 = _make_result("AttributeError", "no attribute 'foo'")
        err2 = _make_result("AttributeError", "no attribute 'bar'")

        tracker.record_error(err1)
        tracker.record_error(err2)
        # Each seen once -- neither repeated
        info1 = tracker.record_error(err1)
        assert info1["repeated"] is True
        assert info1["count"] == 2

        info2 = tracker.record_error(err2)
        assert info2["repeated"] is True
        assert info2["count"] == 2


# ---------------------------------------------------------------------------
# Known error corrections
# ---------------------------------------------------------------------------

class TestKnownCorrections:

    def test_areaproperties_correction(self):
        tracker = ScriptErrorTracker(warn_threshold=1)
        result = _make_result(
            "AttributeError",
            "'BRepBody' object has no attribute 'areaProperties'",
        )
        info = tracker.record_error(result)
        assert info["correction_hint"] is not None
        assert "get_body_properties" in info["correction_hint"]

    def test_volumeproperties_correction(self):
        tracker = ScriptErrorTracker(warn_threshold=1)
        result = _make_result(
            "AttributeError",
            "'BRepBody' object has no attribute 'volumeProperties'",
        )
        info = tracker.record_error(result)
        assert info["correction_hint"] is not None
        assert "get_body_properties" in info["correction_hint"]

    def test_facecount_correction(self):
        tracker = ScriptErrorTracker(warn_threshold=1)
        result = _make_result(
            "AttributeError",
            "'BRepBody' object has no attribute 'faceCount'",
        )
        info = tracker.record_error(result)
        assert info["correction_hint"] is not None
        assert "faces.count" in info["correction_hint"]

    def test_valueinput_correction(self):
        tracker = ScriptErrorTracker(warn_threshold=1)
        result = _make_result(
            "AttributeError",
            "module 'adsk.fusion' has no attribute 'ValueInput'",
        )
        info = tracker.record_error(result)
        assert info["correction_hint"] is not None
        assert "adsk.core" in info["correction_hint"]

    def test_unknown_error_no_correction(self):
        tracker = ScriptErrorTracker(warn_threshold=1)
        result = _make_result("RuntimeError", "some unknown error")
        info = tracker.record_error(result)
        assert info["correction_hint"] is None

    def test_correction_included_in_warning_message(self):
        tracker = ScriptErrorTracker(warn_threshold=2)
        result = _make_result(
            "AttributeError",
            "'BRepBody' object has no attribute 'areaProperties'",
        )
        tracker.record_error(result)
        info = tracker.record_error(result)
        assert "get_body_properties" in info["message"]

    def test_correction_included_in_blocked_message(self):
        tracker = ScriptErrorTracker(warn_threshold=2, block_threshold=3)
        result = _make_result(
            "AttributeError",
            "'BRepBody' object has no attribute 'areaProperties'",
        )
        for _ in range(3):
            info = tracker.record_error(result)
        assert "BLOCKED" in info["message"]
        assert "get_body_properties" in info["message"]


# ---------------------------------------------------------------------------
# _lookup_known_correction helper
# ---------------------------------------------------------------------------

class TestLookupKnownCorrection:

    def test_exact_match(self):
        hint = _lookup_known_correction(
            "AttributeError",
            "'BRepBody' object has no attribute 'areaProperties'",
        )
        assert hint is not None
        assert "get_body_properties" in hint

    def test_no_match(self):
        hint = _lookup_known_correction("ValueError", "something else")
        assert hint is None

    def test_wrong_type_right_message(self):
        # Type must match, even if message matches
        hint = _lookup_known_correction(
            "TypeError",
            "'BRepBody' object has no attribute 'areaProperties'",
        )
        assert hint is None


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:

    def test_reset_clears_counts(self):
        tracker = ScriptErrorTracker(warn_threshold=2)
        result = _make_result("AttributeError", "test error")
        tracker.record_error(result)
        tracker.record_error(result)
        tracker.reset()
        info = tracker.record_error(result)
        assert info["repeated"] is False
        assert info["count"] == 1

    def test_reset_clears_stats(self):
        tracker = ScriptErrorTracker()
        result = _make_result("AttributeError", "test error")
        tracker.record_error(result)
        tracker.reset()
        stats = tracker.get_stats()
        assert stats["tracked_signatures"] == 0
        assert stats["total_errors"] == 0


# ---------------------------------------------------------------------------
# Stats / get_counts
# ---------------------------------------------------------------------------

class TestStats:

    def test_get_counts_returns_copy(self):
        tracker = ScriptErrorTracker()
        result = _make_result("TypeError", "test")
        tracker.record_error(result)
        counts = tracker.get_counts()
        assert ("TypeError", "test") in counts
        # Mutating the copy should not affect tracker
        counts.clear()
        assert tracker.get_counts() != {}

    def test_get_stats_structure(self):
        tracker = ScriptErrorTracker()
        result = _make_result("AttributeError", "msg1")
        tracker.record_error(result)
        tracker.record_error(result)
        stats = tracker.get_stats()
        assert stats["tracked_signatures"] == 1
        assert stats["total_errors"] == 2
        assert "AttributeError:msg1" in stats["signatures"]
        assert stats["signatures"]["AttributeError:msg1"] == 2


# ---------------------------------------------------------------------------
# Invalid / edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_record_error_with_no_signature(self):
        tracker = ScriptErrorTracker()
        info = tracker.record_error({"success": True})
        assert info["repeated"] is False
        assert info["count"] == 0
        assert info["signature"] is None

    def test_record_error_with_none_result(self):
        tracker = ScriptErrorTracker()
        info = tracker.record_error(None)
        assert info["repeated"] is False
        assert info["count"] == 0

    def test_default_thresholds(self):
        assert SCRIPT_ERROR_WARN_THRESHOLD == 2
        assert SCRIPT_ERROR_BLOCK_THRESHOLD == 3

    def test_custom_thresholds(self):
        tracker = ScriptErrorTracker(warn_threshold=5, block_threshold=10)
        result = _make_result("TypeError", "test")
        for i in range(4):
            info = tracker.record_error(result)
        assert info["repeated"] is False  # 4 < 5
        info = tracker.record_error(result)
        assert info["repeated"] is True  # 5 >= 5
        assert info["blocked"] is False  # 5 < 10

    def test_known_corrections_dict_is_populated(self):
        assert len(KNOWN_SCRIPT_ERROR_CORRECTIONS) >= 4
        for key, value in KNOWN_SCRIPT_ERROR_CORRECTIONS.items():
            assert isinstance(key, tuple)
            assert len(key) == 2
            assert isinstance(value, str)
            assert len(value) > 0


# ---------------------------------------------------------------------------
# Integration scenario: simulate the convo_425 pattern
# ---------------------------------------------------------------------------

class TestConvo425Scenario:
    """Simulate the exact failure pattern from convo_425 where
    areaProperties() was called 5 times in different scripts."""

    def test_area_properties_repeated_5_times(self):
        tracker = ScriptErrorTracker(warn_threshold=2, block_threshold=3)
        results = []
        for i in range(5):
            result = _make_result(
                "AttributeError",
                "'BRepBody' object has no attribute 'areaProperties'",
                line_number=i + 1,  # different line numbers = different scripts
            )
            info = tracker.record_error(result)
            results.append(info)

        # 1st call: no repetition
        assert results[0]["repeated"] is False
        assert results[0]["correction_hint"] is not None

        # 2nd call: warning
        assert results[1]["repeated"] is True
        assert results[1]["blocked"] is False
        assert "SCRIPT ERROR REPEATED" in results[1]["message"]

        # 3rd call: blocked
        assert results[2]["blocked"] is True
        assert "BLOCKED" in results[2]["message"]

        # 4th and 5th: still blocked
        assert results[3]["blocked"] is True
        assert results[4]["blocked"] is True
        assert results[4]["count"] == 5

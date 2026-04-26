"""
tests/test_deduplicate_script_error.py
TASK-237: Tests for script error deduplication in conversation history.
"""

import pytest

from ai.tool_recovery import deduplicate_script_error


class TestDeduplicateScriptError:
    """Tests for the deduplicate_script_error function."""

    # ------------------------------------------------------------------
    # Rule 1: stderr/error deduplication
    # ------------------------------------------------------------------

    def test_duplicate_stderr_error_removes_error(self):
        """When stderr and error contain the same traceback, error is removed."""
        traceback_text = (
            "Traceback (most recent call last):\n"
            '  File "<string>", line 5, in <module>\n'
            "AttributeError: 'BRepBody' object has no attribute 'areaProperties'"
        )
        result = {
            "success": False,
            "stderr": traceback_text,
            "error": traceback_text,
            "stdout": "",
        }
        deduped = deduplicate_script_error(result)
        assert "error" not in deduped
        assert deduped["stderr"] == traceback_text
        assert deduped["stdout"] == ""

    def test_error_substring_of_stderr_removes_error(self):
        """When error is a substring of stderr, error is removed."""
        result = {
            "success": False,
            "stderr": "Some wrapper text\nTraceback (most recent call last):\n  File x\nError: bad\nMore text",
            "error": "Traceback (most recent call last):\n  File x\nError: bad",
        }
        deduped = deduplicate_script_error(result)
        assert "error" not in deduped
        assert "stderr" in deduped

    def test_stderr_substring_of_error_removes_error(self):
        """When stderr is a substring of error, error is removed."""
        result = {
            "success": False,
            "stderr": "Error: bad thing happened",
            "error": "Script failed: Error: bad thing happened. Please try again.",
        }
        deduped = deduplicate_script_error(result)
        assert "error" not in deduped

    def test_different_stderr_error_both_kept(self):
        """When stderr and error contain different text, both are kept."""
        result = {
            "success": False,
            "stderr": "Warning: deprecated function used",
            "error": "Script execution timed out after 30 seconds",
        }
        deduped = deduplicate_script_error(result)
        assert "error" in deduped
        assert "stderr" in deduped

    def test_overlapping_traceback_removes_error(self):
        """When both contain overlapping traceback lines, error is removed."""
        stderr = (
            "Running script...\n"
            "Traceback (most recent call last):\n"
            '  File "<string>", line 10, in <module>\n'
            '  File "<string>", line 5, in create_geometry\n'
            "RuntimeError: Cannot create extrude feature\n"
        )
        error = (
            "Script error:\n"
            "Traceback (most recent call last):\n"
            '  File "<string>", line 10, in <module>\n'
            '  File "<string>", line 5, in create_geometry\n'
            "RuntimeError: Cannot create extrude feature"
        )
        result = {
            "success": False,
            "stderr": stderr,
            "error": error,
        }
        deduped = deduplicate_script_error(result)
        assert "error" not in deduped

    def test_empty_stderr_keeps_error(self):
        """When stderr is empty, error is kept."""
        result = {
            "success": False,
            "stderr": "",
            "error": "Something failed",
        }
        deduped = deduplicate_script_error(result)
        assert "error" in deduped

    def test_empty_error_no_change(self):
        """When error is empty, nothing changes."""
        result = {
            "success": False,
            "stderr": "Some error output",
            "error": "",
        }
        deduped = deduplicate_script_error(result)
        assert deduped.get("error") == ""

    def test_no_stderr_keeps_error(self):
        """When stderr key is missing, error is kept."""
        result = {
            "success": False,
            "error": "Something failed",
        }
        deduped = deduplicate_script_error(result)
        assert "error" in deduped

    # ------------------------------------------------------------------
    # Rule 2: diagnostic_data removed when summary present
    # ------------------------------------------------------------------

    def test_diagnostic_data_removed_when_summary_present(self):
        """diagnostic_data removed when diagnostic_summary exists."""
        result = {
            "success": False,
            "stderr": "Error occurred",
            "diagnostic_data": {
                "body_list": {"count": 3, "bodies": ["Box", "Cylinder", "Sphere"]},
            },
            "diagnostic_summary": "[DESIGN STATE] 3 bodies: Box (vol=100), Cylinder (vol=50), Sphere (vol=30)",
        }
        deduped = deduplicate_script_error(result)
        assert "diagnostic_data" not in deduped
        assert "diagnostic_summary" in deduped

    def test_diagnostic_data_kept_when_no_summary(self):
        """diagnostic_data kept when diagnostic_summary is not present."""
        result = {
            "success": False,
            "stderr": "Error occurred",
            "diagnostic_data": {
                "body_list": {"count": 3},
            },
        }
        deduped = deduplicate_script_error(result)
        assert "diagnostic_data" in deduped

    def test_diagnostic_data_kept_when_summary_empty(self):
        """diagnostic_data kept when diagnostic_summary is empty string."""
        result = {
            "success": False,
            "stderr": "Error occurred",
            "diagnostic_data": {"body_list": {"count": 1}},
            "diagnostic_summary": "",
        }
        deduped = deduplicate_script_error(result)
        assert "diagnostic_data" in deduped

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_non_dict_passes_through(self):
        """Non-dict results pass through unchanged."""
        assert deduplicate_script_error("string result") == "string result"
        assert deduplicate_script_error(42) == 42
        assert deduplicate_script_error(None) is None

    def test_non_script_result_passes_through(self):
        """Results without error fields pass through unchanged."""
        result = {
            "success": True,
            "result": "Created box at origin",
        }
        deduped = deduplicate_script_error(result)
        assert deduped == result

    def test_successful_result_unchanged(self):
        """A successful script result should pass through unchanged."""
        result = {
            "success": True,
            "stdout": "Script completed",
            "stderr": "",
        }
        deduped = deduplicate_script_error(result)
        assert deduped == result

    def test_both_rules_applied_together(self):
        """Both deduplication rules should apply in one call."""
        traceback = "Traceback (most recent call last):\n  File x\nError: bad"
        result = {
            "success": False,
            "stderr": traceback,
            "error": traceback,
            "diagnostic_data": {"body_list": {"count": 2}},
            "diagnostic_summary": "[DESIGN STATE] 2 bodies",
        }
        deduped = deduplicate_script_error(result)
        assert "error" not in deduped
        assert "diagnostic_data" not in deduped
        assert "stderr" in deduped
        assert "diagnostic_summary" in deduped

    def test_non_string_error_fields_handled(self):
        """Non-string error/stderr fields should not crash."""
        result = {
            "success": False,
            "stderr": 12345,
            "error": {"nested": "error"},
        }
        deduped = deduplicate_script_error(result)
        # Should not crash, fields kept as-is since they're not strings
        assert "error" in deduped
        assert "stderr" in deduped

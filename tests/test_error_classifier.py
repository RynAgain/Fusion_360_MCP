"""
tests/test_error_classifier.py
Unit tests for ai/error_classifier.py -- error classification, suggestion
generation, auto-undo decisions, error enrichment, and script error parsing.
"""
import pytest
from ai.error_classifier import (
    classify_error,
    get_suggestion,
    should_auto_undo,
    enrich_error,
    parse_script_error,
    GEOMETRY_ERROR,
    REFERENCE_ERROR,
    PARAMETER_ERROR,
    SCRIPT_ERROR,
    CONNECTION_ERROR,
    TIMEOUT_ERROR,
    UNKNOWN_ERROR,
)


# ---------------------------------------------------------------------------
# classify_error
# ---------------------------------------------------------------------------

class TestClassifyError:
    """Pattern-based error classification."""

    def test_geometry_feature_failed(self):
        assert classify_error("Feature failed to create") == GEOMETRY_ERROR

    def test_geometry_self_intersecting(self):
        assert classify_error("Self-intersecting geometry") == GEOMETRY_ERROR

    def test_geometry_no_profile(self):
        assert classify_error("No profile found in sketch") == GEOMETRY_ERROR

    def test_reference_not_found(self):
        assert classify_error("Body 'Foo' not found") == REFERENCE_ERROR

    def test_reference_index_out_of_range(self):
        assert classify_error("Index out of range") == REFERENCE_ERROR

    def test_parameter_must_be_positive(self):
        assert classify_error("Value must be positive") == PARAMETER_ERROR

    def test_parameter_too_small(self):
        assert classify_error("Radius too small") == PARAMETER_ERROR

    def test_script_type_error(self):
        assert classify_error("TypeError: expected int") == SCRIPT_ERROR

    def test_script_traceback(self):
        assert classify_error(
            "Traceback (most recent call last):\n  line 5"
        ) == SCRIPT_ERROR

    def test_connection_refused(self):
        assert classify_error("Connection refused") == CONNECTION_ERROR

    def test_connection_socket_error(self):
        assert classify_error("Socket error on send") == CONNECTION_ERROR

    def test_timeout(self):
        assert classify_error("Operation timed out") == TIMEOUT_ERROR

    def test_unknown_for_unexpected(self):
        assert classify_error("Something completely unexpected") == UNKNOWN_ERROR

    def test_unknown_for_empty(self):
        assert classify_error("") == UNKNOWN_ERROR


# ---------------------------------------------------------------------------
# get_suggestion
# ---------------------------------------------------------------------------

class TestGetSuggestion:
    """Suggestion generation per error type / tool."""

    def test_geometry_default(self):
        s = get_suggestion(GEOMETRY_ERROR)
        assert "undo" in s.lower() or "Undo" in s

    def test_geometry_specific_tool(self):
        s = get_suggestion(GEOMETRY_ERROR, "extrude")
        assert "profile" in s.lower() or "sketch" in s.lower()

    def test_reference_default(self):
        s = get_suggestion(REFERENCE_ERROR)
        assert "not found" in s.lower() or "get_body_list" in s

    def test_parameter_default(self):
        s = get_suggestion(PARAMETER_ERROR)
        assert "positive" in s.lower() or "centimeters" in s.lower()

    def test_script_default(self):
        s = get_suggestion(SCRIPT_ERROR)
        assert "traceback" in s.lower() or "fix" in s.lower()

    def test_unknown_type_returns_fallback(self):
        s = get_suggestion("NONEXISTENT_TYPE")
        assert isinstance(s, str) and len(s) > 0


# ---------------------------------------------------------------------------
# should_auto_undo
# ---------------------------------------------------------------------------

class TestShouldAutoUndo:
    """Auto-undo decisions based on error type + tool name."""

    def test_geometry_error_with_extrude(self):
        assert should_auto_undo(GEOMETRY_ERROR, "extrude") is True

    def test_geometry_error_with_add_fillet(self):
        assert should_auto_undo(GEOMETRY_ERROR, "add_fillet") is True

    def test_geometry_error_with_non_geometry_tool(self):
        assert should_auto_undo(GEOMETRY_ERROR, "get_body_list") is False

    def test_non_geometry_error_with_geometry_tool(self):
        assert should_auto_undo(REFERENCE_ERROR, "extrude") is False

    def test_timeout_error_with_geometry_tool(self):
        assert should_auto_undo(TIMEOUT_ERROR, "create_box") is True

    def test_timeout_error_with_query_tool(self):
        assert should_auto_undo(TIMEOUT_ERROR, "get_body_list") is False


# ---------------------------------------------------------------------------
# enrich_error
# ---------------------------------------------------------------------------

class TestEnrichError:
    """Error result enrichment with classification metadata."""

    def test_enriches_with_type_and_details(self):
        result = {"success": False, "error": "Feature failed"}
        enriched = enrich_error("extrude", "Feature failed", result)
        assert enriched["error_type"] == GEOMETRY_ERROR
        assert "error_details" in enriched
        assert "suggestion" in enriched["error_details"]
        assert enriched["error_details"]["tool_name"] == "extrude"

    def test_preserves_original_fields(self):
        result = {"success": False, "error": "Not found", "extra": "data"}
        enriched = enrich_error("select", "Not found", result)
        assert enriched["extra"] == "data"

    def test_creates_minimal_result_when_none(self):
        enriched = enrich_error("extrude", "Feature failed", None)
        assert enriched["success"] is False
        assert enriched["error"] == "Feature failed"
        assert "error_type" in enriched

    def test_error_details_has_auto_undo_recommended(self):
        result = {"success": False, "error": "Feature failed"}
        enriched = enrich_error("extrude", "Feature failed", result)
        assert "auto_undo_recommended" in enriched["error_details"]


# ---------------------------------------------------------------------------
# parse_script_error
# ---------------------------------------------------------------------------

class TestParseScriptError:
    """Traceback parsing for execute_script errors."""

    def test_parses_traceback(self):
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "<string>", line 5, in <module>\n'
            "    x = y + 1\n"
            "NameError: name 'y' is not defined"
        )
        info = parse_script_error(stderr)
        assert info["line_number"] == 5
        assert info["error_type"] == "NameError"
        assert "not defined" in info["error_message"]

    def test_handles_empty_string(self):
        info = parse_script_error("")
        assert info["line_number"] is None
        assert info["error_type"] is None

    def test_handles_single_line_error(self):
        info = parse_script_error("SyntaxError: invalid syntax")
        assert info["error_type"] == "SyntaxError"
        assert "invalid syntax" in info["error_message"]

    def test_extracts_relevant_line(self):
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "script.py", line 10, in func\n'
            "    return foo.bar()\n"
            "AttributeError: 'NoneType' object has no attribute 'bar'"
        )
        info = parse_script_error(stderr)
        assert info["relevant_line"] == "return foo.bar()"
        assert info["line_number"] == 10

"""
tests/test_tool_recovery.py
TASK-225: Unit tests for ai/tool_recovery.py -- centralized tool-category-aware
recovery strategies.
"""

import pytest

from ai.tool_recovery import (
    CAD_TOOLS,
    DOCUMENT_TOOLS,
    FILE_TOOLS,
    WEB_TOOLS,
    get_recovery_strategy,
    get_tool_category,
)


# ---------------------------------------------------------------------------
# get_tool_category
# ---------------------------------------------------------------------------

class TestGetToolCategory:
    """Validate tool -> category mapping."""

    def test_web_tools_return_web(self):
        for tool in ("web_search", "web_fetch", "fusion_docs_search"):
            assert get_tool_category(tool) == "web"

    def test_cad_tools_return_cad(self):
        for tool in ("extrude", "revolve", "get_body_list", "create_box"):
            assert get_tool_category(tool) == "cad"

    def test_file_tools_return_file(self):
        for tool in ("write_file", "apply_diff", "list_files"):
            assert get_tool_category(tool) == "file"

    def test_document_tools_return_document(self):
        assert get_tool_category("read_document") == "document"

    def test_unknown_tool_returns_unknown(self):
        assert get_tool_category("nonexistent_tool") == "unknown"

    def test_empty_string_returns_unknown(self):
        assert get_tool_category("") == "unknown"


# ---------------------------------------------------------------------------
# get_recovery_strategy -- return shape
# ---------------------------------------------------------------------------

class TestRecoveryStrategyShape:
    """All strategies must return the documented dict shape."""

    _REQUIRED_KEYS = {
        "suggestion",
        "should_inject_system_message",
        "system_message",
        "should_block_retry",
    }

    @pytest.mark.parametrize("tool_name,error_type,failures", [
        ("web_search", "REFERENCE_ERROR", 0),
        ("web_search", "UNKNOWN_ERROR", 3),
        ("extrude", "GEOMETRY_ERROR", 1),
        ("write_file", "REFERENCE_ERROR", 5),
        ("read_document", "UNKNOWN_ERROR", 4),
        ("nonexistent_tool", "UNKNOWN_ERROR", 0),
    ])
    def test_strategy_has_required_keys(self, tool_name, error_type, failures):
        strategy = get_recovery_strategy(tool_name, error_type, failures)
        assert set(strategy.keys()) == self._REQUIRED_KEYS

    def test_suggestion_is_string(self):
        strategy = get_recovery_strategy("web_search", "TIMEOUT_ERROR", 1)
        assert isinstance(strategy["suggestion"], str)
        assert len(strategy["suggestion"]) > 0

    def test_should_inject_is_bool(self):
        strategy = get_recovery_strategy("web_search", "TIMEOUT_ERROR", 1)
        assert isinstance(strategy["should_inject_system_message"], bool)

    def test_system_message_is_string(self):
        strategy = get_recovery_strategy("web_search", "TIMEOUT_ERROR", 1)
        assert isinstance(strategy["system_message"], str)

    def test_should_block_is_bool(self):
        strategy = get_recovery_strategy("web_search", "TIMEOUT_ERROR", 1)
        assert isinstance(strategy["should_block_retry"], bool)


# ---------------------------------------------------------------------------
# Web tool recovery
# ---------------------------------------------------------------------------

class TestWebRecovery:
    """Web tools: after budget, suggest asking user; never suggest CAD diagnostics."""

    def test_below_budget_no_system_message(self):
        strategy = get_recovery_strategy("web_search", "REFERENCE_ERROR", 1)
        assert strategy["should_inject_system_message"] is False
        assert strategy["should_block_retry"] is False

    def test_at_budget_triggers_system_message(self):
        # Default budget is 3
        strategy = get_recovery_strategy("web_search", "UNKNOWN_ERROR", 3)
        assert strategy["should_inject_system_message"] is True
        assert strategy["should_block_retry"] is True
        assert "ask the user" in strategy["system_message"].lower()

    def test_above_budget_triggers_system_message(self):
        strategy = get_recovery_strategy("web_fetch", "CONNECTION_ERROR", 5)
        assert strategy["should_inject_system_message"] is True
        assert strategy["should_block_retry"] is True

    def test_web_suggestions_never_mention_cad(self):
        """Web recovery must never suggest CAD diagnostics."""
        for failures in range(0, 6):
            for error in ("REFERENCE_ERROR", "CONNECTION_ERROR", "TIMEOUT_ERROR", "UNKNOWN_ERROR"):
                strategy = get_recovery_strategy("web_search", error, failures)
                suggestion = strategy["suggestion"].lower()
                msg = strategy["system_message"].lower()
                assert "get_body_list" not in suggestion
                assert "get_body_list" not in msg
                assert "get_timeline" not in suggestion
                assert "get_timeline" not in msg

    def test_reference_error_has_url_suggestion(self):
        strategy = get_recovery_strategy("web_fetch", "REFERENCE_ERROR", 1)
        assert "url" in strategy["suggestion"].lower()

    def test_timeout_error_has_timeout_suggestion(self):
        strategy = get_recovery_strategy("web_search", "TIMEOUT_ERROR", 1)
        assert "timed out" in strategy["suggestion"].lower()

    def test_fusion_docs_search_is_web_category(self):
        strategy = get_recovery_strategy("fusion_docs_search", "UNKNOWN_ERROR", 3)
        assert strategy["should_inject_system_message"] is True
        assert strategy["should_block_retry"] is True


# ---------------------------------------------------------------------------
# CAD tool recovery
# ---------------------------------------------------------------------------

class TestCADRecovery:
    """CAD tools: suggest get_body_list/get_timeline; never suggest web alternatives."""

    def test_below_budget_no_system_message(self):
        strategy = get_recovery_strategy("extrude", "GEOMETRY_ERROR", 1)
        assert strategy["should_inject_system_message"] is False

    def test_at_budget_injects_system_message(self):
        # Default CAD budget is 5
        strategy = get_recovery_strategy("extrude", "GEOMETRY_ERROR", 5)
        assert strategy["should_inject_system_message"] is True
        assert "get_body_list" in strategy["system_message"]

    def test_cad_never_blocks_retry(self):
        """CAD tools may succeed with different params, so never block."""
        strategy = get_recovery_strategy("extrude", "GEOMETRY_ERROR", 10)
        assert strategy["should_block_retry"] is False

    def test_cad_suggestions_never_mention_web(self):
        """CAD recovery must never suggest web alternatives."""
        for tool in ("extrude", "revolve", "add_fillet", "add_chamfer"):
            for failures in range(0, 8):
                strategy = get_recovery_strategy(tool, "GEOMETRY_ERROR", failures)
                suggestion = strategy["suggestion"].lower()
                msg = strategy["system_message"].lower()
                assert "web_search" not in suggestion
                assert "web_search" not in msg
                assert "web_fetch" not in suggestion
                assert "web_fetch" not in msg

    def test_extrude_suggests_sketch_info(self):
        strategy = get_recovery_strategy("extrude", "GEOMETRY_ERROR", 1)
        assert "get_sketch_info" in strategy["suggestion"]

    def test_revolve_suggests_sketch_info(self):
        strategy = get_recovery_strategy("revolve", "GEOMETRY_ERROR", 1)
        assert "get_sketch_info" in strategy["suggestion"]

    def test_fillet_suggests_smaller_value(self):
        strategy = get_recovery_strategy("add_fillet", "GEOMETRY_ERROR", 1)
        assert "smaller" in strategy["suggestion"].lower()

    def test_chamfer_suggests_smaller_value(self):
        strategy = get_recovery_strategy("add_chamfer", "GEOMETRY_ERROR", 1)
        assert "smaller" in strategy["suggestion"].lower()

    def test_execute_script_suggests_smaller_steps(self):
        strategy = get_recovery_strategy("execute_script", "SCRIPT_ERROR", 1)
        assert "smaller" in strategy["suggestion"].lower() or "break" in strategy["suggestion"].lower()

    def test_generic_cad_tool_gets_default(self):
        strategy = get_recovery_strategy("get_body_list", "UNKNOWN_ERROR", 1)
        assert "get_body_list" in strategy["suggestion"]


# ---------------------------------------------------------------------------
# File tool recovery
# ---------------------------------------------------------------------------

class TestFileRecovery:
    """File tools: suggest checking file paths."""

    def test_below_budget_no_system_message(self):
        strategy = get_recovery_strategy("write_file", "REFERENCE_ERROR", 1)
        assert strategy["should_inject_system_message"] is False

    def test_at_budget_injects_system_message(self):
        # Default file budget is 3
        strategy = get_recovery_strategy("write_file", "REFERENCE_ERROR", 3)
        assert strategy["should_inject_system_message"] is True

    def test_file_suggestion_mentions_path(self):
        strategy = get_recovery_strategy("apply_diff", "REFERENCE_ERROR", 1)
        assert "path" in strategy["suggestion"].lower()

    def test_file_never_blocks_retry(self):
        strategy = get_recovery_strategy("write_file", "REFERENCE_ERROR", 10)
        assert strategy["should_block_retry"] is False


# ---------------------------------------------------------------------------
# Document tool recovery
# ---------------------------------------------------------------------------

class TestDocumentRecovery:
    """Document tools: suggest format check or asking user."""

    def test_below_budget_no_system_message(self):
        strategy = get_recovery_strategy("read_document", "UNKNOWN_ERROR", 1)
        assert strategy["should_inject_system_message"] is False

    def test_at_budget_injects_system_message(self):
        # Default document budget is 3
        strategy = get_recovery_strategy("read_document", "UNKNOWN_ERROR", 3)
        assert strategy["should_inject_system_message"] is True
        assert "ask the user" in strategy["system_message"].lower()

    def test_document_suggestion_mentions_format(self):
        strategy = get_recovery_strategy("read_document", "UNKNOWN_ERROR", 1)
        assert "format" in strategy["suggestion"].lower()


# ---------------------------------------------------------------------------
# Unknown tool recovery
# ---------------------------------------------------------------------------

class TestUnknownRecovery:
    """Unknown tools: generic fallback."""

    def test_unknown_tool_returns_generic(self):
        strategy = get_recovery_strategy("nonexistent_tool", "UNKNOWN_ERROR", 0)
        assert strategy["should_inject_system_message"] is False
        assert strategy["should_block_retry"] is False
        assert "different approach" in strategy["suggestion"].lower()

    def test_unknown_tool_high_failures_still_no_inject(self):
        """Unknown tools never inject system messages (no budget)."""
        strategy = get_recovery_strategy("mystery_tool", "UNKNOWN_ERROR", 100)
        assert strategy["should_inject_system_message"] is False


# ---------------------------------------------------------------------------
# Category set consistency
# ---------------------------------------------------------------------------

class TestCategorySets:
    """Verify category sets are non-empty and non-overlapping."""

    def test_web_tools_non_empty(self):
        assert len(WEB_TOOLS) > 0

    def test_cad_tools_non_empty(self):
        assert len(CAD_TOOLS) > 0

    def test_file_tools_non_empty(self):
        assert len(FILE_TOOLS) > 0

    def test_document_tools_non_empty(self):
        assert len(DOCUMENT_TOOLS) > 0

    def test_web_and_cad_disjoint(self):
        assert WEB_TOOLS.isdisjoint(CAD_TOOLS)

    def test_web_and_file_disjoint(self):
        assert WEB_TOOLS.isdisjoint(FILE_TOOLS)

    def test_cad_and_file_disjoint(self):
        assert CAD_TOOLS.isdisjoint(FILE_TOOLS)

    def test_read_document_in_both_file_and_document(self):
        """read_document is in FILE_TOOLS (from repetition_detector)
        and DOCUMENT_TOOLS -- document takes precedence in category lookup."""
        # get_tool_category checks DOCUMENT_TOOLS before FILE_TOOLS,
        # so read_document always resolves to "document".
        category = get_tool_category("read_document")
        assert category == "document"

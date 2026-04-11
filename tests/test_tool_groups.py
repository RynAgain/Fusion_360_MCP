"""
tests/test_tool_groups.py
Tests for the MCP tool group definitions and filtering.
"""

import pytest

from mcp.tool_groups import (
    TOOL_GROUPS,
    get_tools_for_groups,
    get_all_tool_names,
    filter_tool_definitions,
)


class TestToolGroups:
    """Tests for TOOL_GROUPS constant."""

    def test_all_groups_exist(self):
        """All expected group keys are present."""
        expected = {
            "document", "sketch", "primitives", "features",
            "body_ops", "query", "utility", "export",
            "vision", "scripting",
        }
        assert set(TOOL_GROUPS.keys()) == expected

    def test_no_empty_groups(self):
        """Every group contains at least one tool."""
        for group_name, tools in TOOL_GROUPS.items():
            assert len(tools) > 0, f"Group '{group_name}' is empty"

    def test_no_duplicate_tool_names_within_group(self):
        """No group contains duplicate tool names."""
        for group_name, tools in TOOL_GROUPS.items():
            assert len(tools) == len(set(tools)), (
                f"Group '{group_name}' has duplicate tool names"
            )


class TestGetToolsForGroups:
    """Tests for get_tools_for_groups()."""

    def test_single_group(self):
        """Requesting a single group returns its tools."""
        tools = get_tools_for_groups(["vision"])
        assert tools == {"take_screenshot"}

    def test_multiple_groups(self):
        """Requesting multiple groups returns the union."""
        tools = get_tools_for_groups(["vision", "scripting"])
        assert tools == {"take_screenshot", "execute_script"}

    def test_empty_groups(self):
        """An empty list returns an empty set."""
        tools = get_tools_for_groups([])
        assert tools == set()

    def test_unknown_group_ignored(self):
        """An unknown group name is silently ignored."""
        tools = get_tools_for_groups(["nonexistent_group"])
        assert tools == set()

    def test_sketch_group_contents(self):
        """The sketch group has the expected tools."""
        tools = get_tools_for_groups(["sketch"])
        expected = {
            "create_sketch", "add_sketch_line", "add_sketch_circle",
            "add_sketch_rectangle", "add_sketch_arc",
        }
        assert tools == expected

    def test_document_group_contents(self):
        """The document group has the expected tools."""
        tools = get_tools_for_groups(["document"])
        expected = {
            "get_document_info", "save_document", "list_documents",
            "switch_document", "new_document", "close_document",
        }
        assert tools == expected


class TestGetAllToolNames:
    """Tests for get_all_tool_names()."""

    def test_returns_all_tools(self):
        """All tools across all groups are returned."""
        all_tools = get_all_tool_names()
        # Count the unique tools by flattening TOOL_GROUPS
        expected_count = len(set(
            t for tools in TOOL_GROUPS.values() for t in tools
        ))
        assert len(all_tools) == expected_count

    def test_contains_known_tools(self):
        """Known tools from various groups are in the result."""
        all_tools = get_all_tool_names()
        assert "create_cylinder" in all_tools
        assert "take_screenshot" in all_tools
        assert "execute_script" in all_tools
        assert "extrude" in all_tools
        assert "save_document" in all_tools
        assert "undo" in all_tools
        assert "export_stl" in all_tools

    def test_returns_set(self):
        """The return type is a set."""
        assert isinstance(get_all_tool_names(), set)


class TestFilterToolDefinitions:
    """Tests for filter_tool_definitions()."""

    def test_filters_correctly(self):
        """Only definitions with names in the allowed set are kept."""
        definitions = [
            {"name": "tool_a", "description": "A"},
            {"name": "tool_b", "description": "B"},
            {"name": "tool_c", "description": "C"},
        ]
        allowed = {"tool_a", "tool_c"}
        result = filter_tool_definitions(definitions, allowed)
        assert len(result) == 2
        names = {d["name"] for d in result}
        assert names == {"tool_a", "tool_c"}

    def test_empty_allowed_returns_empty(self):
        """An empty allowed set produces an empty list."""
        definitions = [
            {"name": "tool_a", "description": "A"},
        ]
        result = filter_tool_definitions(definitions, set())
        assert result == []

    def test_empty_definitions_returns_empty(self):
        """An empty definitions list produces an empty list."""
        result = filter_tool_definitions([], {"tool_a"})
        assert result == []

    def test_preserves_definition_structure(self):
        """Filtered definitions retain their original structure."""
        definitions = [
            {"name": "tool_a", "description": "A", "input_schema": {"type": "object"}},
        ]
        result = filter_tool_definitions(definitions, {"tool_a"})
        assert result[0] == definitions[0]

"""
tests/test_mcp_server.py
Unit tests for mcp/server.py -- MCPServer tool registry, dispatch, and hooks.

Verifies 27 tool definitions, category mapping, schema validity, and
tool execution through the bridge in simulation mode.
"""

import pytest
from unittest.mock import MagicMock
from fusion.bridge import FusionBridge
from mcp.server import MCPServer, TOOL_DEFINITIONS, TOOL_CATEGORIES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge():
    return FusionBridge()


@pytest.fixture
def server(bridge):
    return MCPServer(bridge)


# ---------------------------------------------------------------------------
# Tool definitions -- static validation
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    """Validate the static TOOL_DEFINITIONS list is well-formed."""

    def test_is_list(self):
        assert isinstance(TOOL_DEFINITIONS, list)

    def test_has_exactly_48_tools(self):
        assert len(TOOL_DEFINITIONS) == 48

    def test_all_have_name(self):
        for tool in TOOL_DEFINITIONS:
            assert isinstance(tool["name"], str)

    def test_all_have_description(self):
        for tool in TOOL_DEFINITIONS:
            assert isinstance(tool["description"], str)
            assert len(tool["description"]) > 0

    def test_all_have_input_schema_object(self):
        for tool in TOOL_DEFINITIONS:
            schema = tool["input_schema"]
            assert schema["type"] == "object"
            assert "properties" in schema
            assert "required" in schema

    def test_no_duplicate_names(self):
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert len(names) == len(set(names))

    def test_get_body_list_in_definitions(self):
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert "get_body_list" in names

    def test_get_body_list_no_required_params(self):
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "get_body_list")
        assert tool["input_schema"]["required"] == []
        assert tool["input_schema"]["properties"] == {}

    @pytest.mark.parametrize("expected_name", [
        "get_document_info", "create_cylinder", "create_box", "create_sphere",
        "get_body_list", "take_screenshot", "execute_script", "undo",
        "save_document", "create_sketch", "add_sketch_line", "add_sketch_circle",
        "add_sketch_rectangle", "add_sketch_arc", "extrude", "revolve",
        "add_fillet", "add_chamfer", "delete_body", "mirror_body", "create_component",
        "apply_material", "export_stl", "export_step", "export_f3d",
        "redo", "get_timeline", "set_parameter",
        "get_body_properties", "get_sketch_info", "get_face_info",
        "measure_distance", "get_component_info", "validate_design",
        "edit_feature", "suppress_feature", "delete_feature",
        "reorder_feature", "save_document_as",
    ])
    def test_expected_tool_present(self, expected_name):
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert expected_name in names


# ---------------------------------------------------------------------------
# Tool categories
# ---------------------------------------------------------------------------

class TestToolCategories:
    """Validate TOOL_CATEGORIES completeness and category values."""

    def test_all_tools_have_category(self):
        for tool in TOOL_DEFINITIONS:
            assert tool["name"] in TOOL_CATEGORIES, (
                f"Tool '{tool['name']}' missing from TOOL_CATEGORIES"
            )

    def test_categories_count_matches_definitions(self):
        defined_names = {t["name"] for t in TOOL_DEFINITIONS}
        categorised_names = set(TOOL_CATEGORIES.keys())
        assert defined_names == categorised_names

    def test_get_body_list_category(self):
        assert TOOL_CATEGORIES["get_body_list"] == "Document"

    @pytest.mark.parametrize("category", [
        "Vision", "Scripting", "Sketching", "Features", "Body Operations", "Export",
    ])
    def test_expected_categories_exist(self, category):
        values = set(TOOL_CATEGORIES.values())
        assert category in values, f"Category '{category}' not found in TOOL_CATEGORIES values"

    def test_take_screenshot_is_vision(self):
        assert TOOL_CATEGORIES["take_screenshot"] == "Vision"

    def test_execute_script_is_scripting(self):
        assert TOOL_CATEGORIES["execute_script"] == "Scripting"

    def test_create_sketch_is_sketching(self):
        assert TOOL_CATEGORIES["create_sketch"] == "Sketching"

    def test_extrude_is_features(self):
        assert TOOL_CATEGORIES["extrude"] == "Features"

    def test_mirror_body_is_body_operations(self):
        assert TOOL_CATEGORIES["mirror_body"] == "Body Operations"

    def test_export_stl_is_export(self):
        assert TOOL_CATEGORIES["export_stl"] == "Export"


# ---------------------------------------------------------------------------
# MCPServer -- introspection
# ---------------------------------------------------------------------------

class TestMCPServerIntrospection:
    """Test MCPServer property/method accessors."""

    def test_tool_definitions_property(self, server):
        defs = server.tool_definitions
        assert isinstance(defs, list)
        assert len(defs) == 48

    def test_get_tool_names(self, server):
        names = server.get_tool_names()
        assert "get_body_list" in names
        assert "create_cylinder" in names
        assert "take_screenshot" in names
        assert "delete_body" in names
        assert len(names) == 48

    def test_describe_tools_includes_categories(self, server):
        desc = server.describe_tools()
        assert "get_body_list" in desc
        assert "[Document]" in desc
        assert "[Vision]" in desc


# ---------------------------------------------------------------------------
# MCPServer -- tool execution
# ---------------------------------------------------------------------------

class TestMCPServerExecution:
    """Test tool execution through MCPServer dispatch.

    Since the bridge is not connected, all tool executions return
    a connection error dict -- no simulation fallback.
    """

    def test_execute_get_body_list_not_connected(self, server):
        result = server.execute_tool("get_body_list", {})
        assert result["status"] == "error"
        assert "Not connected" in result["message"]

    def test_execute_unknown_tool(self, server):
        result = server.execute_tool("fake_tool_xyz", {})
        assert result["status"] == "error"

    def test_execute_create_sketch_not_connected(self, server):
        result = server.execute_tool("create_sketch", {"plane": "XY"})
        assert result["status"] == "error"
        assert "Not connected" in result["message"]

    def test_execute_extrude_not_connected(self, server):
        result = server.execute_tool("extrude", {
            "sketch_name": "Sketch1", "distance": 5.0,
        })
        assert result["status"] == "error"
        assert "Not connected" in result["message"]

    def test_execute_take_screenshot_not_connected(self, server):
        result = server.execute_tool("take_screenshot", {})
        assert result["status"] == "error"
        assert "Not connected" in result["message"]

    def test_execute_export_stl_not_connected(self, server):
        result = server.execute_tool("export_stl", {"filename": "out.stl"})
        assert result["status"] == "error"
        assert "Not connected" in result["message"]

    def test_execute_set_parameter_not_connected(self, server):
        result = server.execute_tool("set_parameter", {"name": "d", "value": "5 mm"})
        assert result["status"] == "error"
        assert "Not connected" in result["message"]

    def test_execute_delete_body_not_connected(self, server):
        result = server.execute_tool("delete_body", {"body_name": "Body1"})
        assert result["status"] == "error"
        assert "Not connected" in result["message"]


# ---------------------------------------------------------------------------
# MCPServer -- hooks
# ---------------------------------------------------------------------------

class TestMCPServerHooks:
    """Test pre- and post-execution hooks.

    With no connected bridge, tool execution returns error dicts.
    Hooks should still fire correctly.
    """

    def test_pre_hook_allows(self, server):
        called = []
        def hook(name, inputs):
            called.append(name)
            return True
        server.add_pre_hook(hook)
        result = server.execute_tool("get_body_list", {})
        # Hook was called even though bridge is not connected
        assert called == ["get_body_list"]
        assert result["status"] == "error"

    def test_pre_hook_cancels(self, server):
        server.add_pre_hook(lambda name, inputs: False)
        result = server.execute_tool("get_body_list", {})
        assert result["status"] == "cancelled"

    def test_post_hook_receives_result(self, server):
        captured = []
        def hook(name, inputs, result):
            captured.append((name, result))
        server.add_post_hook(hook)
        server.execute_tool("get_body_list", {})
        assert len(captured) == 1
        assert captured[0][0] == "get_body_list"
        # Result will be an error dict since bridge is not connected
        assert captured[0][1]["status"] == "error"

    def test_post_hook_error_does_not_crash(self, server):
        def bad_hook(name, inputs, result):
            raise RuntimeError("intentional test error")
        server.add_post_hook(bad_hook)
        result = server.execute_tool("get_body_list", {})
        # Should not crash, returns error for not connected
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# MCPServer -- web search tool dispatch
# ---------------------------------------------------------------------------

class TestMCPServerWebDispatch:
    """Verify web search tools are dispatched to WebSearchProvider, not FusionBridge."""

    def test_web_search_dispatched_to_web_provider(self):
        """web_search should NOT go through FusionBridge."""
        mock_bridge = MagicMock()
        mock_bridge.connected = False
        server = MCPServer(mock_bridge)

        # Mock the web search provider
        server._web_search = MagicMock()
        server._web_search.search.return_value = []

        result = server.execute_tool("web_search", {"query": "test"})

        # Should have called web search, NOT the bridge
        server._web_search.search.assert_called_once_with("test", max_results=5)
        mock_bridge.execute.assert_not_called()

    def test_web_fetch_dispatched_to_web_provider(self):
        """web_fetch should NOT go through FusionBridge."""
        mock_bridge = MagicMock()
        server = MCPServer(mock_bridge)
        server._web_search = MagicMock()
        server._web_search.fetch_page.return_value = {
            "url": "https://example.com",
            "title": "Example",
            "content": "page content",
            "success": True,
            "error": None,
        }

        result = server.execute_tool("web_fetch", {"url": "https://example.com"})

        server._web_search.fetch_page.assert_called_once_with(
            "https://example.com", max_chars=10000,
        )
        mock_bridge.execute.assert_not_called()

    def test_fusion_docs_search_dispatched_to_web_provider(self):
        """fusion_docs_search should NOT go through FusionBridge."""
        mock_bridge = MagicMock()
        server = MCPServer(mock_bridge)
        server._web_search = MagicMock()
        server._web_search.search_fusion_docs.return_value = []

        result = server.execute_tool("fusion_docs_search", {"query": "sketch"})

        server._web_search.search_fusion_docs.assert_called_once_with("sketch")
        mock_bridge.execute.assert_not_called()

    def test_web_search_custom_max_results(self):
        """web_search should forward the max_results parameter."""
        mock_bridge = MagicMock()
        server = MCPServer(mock_bridge)
        server._web_search = MagicMock()
        server._web_search.search.return_value = []

        server.execute_tool("web_search", {"query": "fusion api", "max_results": 10})

        server._web_search.search.assert_called_once_with("fusion api", max_results=10)

    def test_web_fetch_custom_max_chars(self):
        """web_fetch should forward the max_chars parameter."""
        mock_bridge = MagicMock()
        server = MCPServer(mock_bridge)
        server._web_search = MagicMock()
        server._web_search.fetch_page.return_value = {"success": True}

        server.execute_tool("web_fetch", {"url": "https://example.com", "max_chars": 5000})

        server._web_search.fetch_page.assert_called_once_with(
            "https://example.com", max_chars=5000,
        )

    def test_web_tool_exception_returns_error(self):
        """If a web search tool raises, return an error dict instead of crashing."""
        mock_bridge = MagicMock()
        server = MCPServer(mock_bridge)
        server._web_search = MagicMock()
        server._web_search.search.side_effect = RuntimeError("network down")

        result = server.execute_tool("web_search", {"query": "test"})

        assert result["status"] == "error"
        assert "network down" in result["error"]
        mock_bridge.execute.assert_not_called()


# ---------------------------------------------------------------------------
# TASK-226: Unknown command detection and blocklist
# ---------------------------------------------------------------------------

class TestUnknownCommandBlocklist:
    """TASK-226: Verify that 'Unknown command' errors are enhanced and
    the tool is added to a session blocklist for fast-fail on retry."""

    def test_unknown_command_is_detected_and_blocklisted(self):
        """When the bridge returns 'Unknown command', the tool is blocklisted."""
        mock_bridge = MagicMock()
        mock_bridge.execute.return_value = {
            "status": "error",
            "message": "Unknown command: 'edit_feature'",
        }
        server = MCPServer(mock_bridge)

        result = server.execute_tool("edit_feature", {"timeline_index": 0})

        assert result["status"] == "error"
        assert result.get("blocklisted") is True
        assert "not available" in result["message"]
        assert "Do not retry" in result["message"]
        assert "edit_feature" in server.blocklisted_tools

    def test_blocklisted_tool_returns_cached_error_immediately(self):
        """Once blocklisted, the tool returns instantly without calling bridge."""
        mock_bridge = MagicMock()
        server = MCPServer(mock_bridge)
        # Manually add to blocklist
        server._blocklisted_tools.add("suppress_feature")

        result = server.execute_tool("suppress_feature", {"timeline_index": 0})

        assert result["status"] == "error"
        assert result.get("blocklisted") is True
        assert "not available" in result["message"]
        # Bridge should NOT have been called
        mock_bridge.execute.assert_not_called()

    def test_non_unknown_command_error_not_blocklisted(self):
        """Regular errors should NOT trigger blocklisting."""
        mock_bridge = MagicMock()
        mock_bridge.execute.return_value = {
            "status": "error",
            "message": "Body 'MyBody' not found",
        }
        server = MCPServer(mock_bridge)

        result = server.execute_tool("delete_body", {"body_name": "MyBody"})

        assert result["status"] == "error"
        assert result.get("blocklisted") is not True
        assert "delete_body" not in server.blocklisted_tools

    def test_success_response_not_blocklisted(self):
        """Successful responses should not affect the blocklist."""
        mock_bridge = MagicMock()
        mock_bridge.execute.return_value = {
            "status": "success",
            "message": "Body list retrieved",
        }
        server = MCPServer(mock_bridge)

        result = server.execute_tool("get_body_list", {})

        assert result["status"] == "success"
        assert "get_body_list" not in server.blocklisted_tools

    def test_clear_blocklist(self):
        """clear_blocklist() resets both blocklist and availability cache."""
        mock_bridge = MagicMock()
        server = MCPServer(mock_bridge)
        server._blocklisted_tools.add("edit_feature")
        server._addin_available_tools = {"get_body_list"}

        server.clear_blocklist()

        assert len(server.blocklisted_tools) == 0
        assert server._addin_available_tools is None

    def test_multiple_tools_can_be_blocklisted(self):
        """Multiple different tools can be added to the blocklist."""
        mock_bridge = MagicMock()
        mock_bridge.execute.return_value = {
            "status": "error",
            "message": "Unknown command: 'test_tool'",
        }
        server = MCPServer(mock_bridge)

        server.execute_tool("edit_feature", {})
        mock_bridge.execute.return_value = {
            "status": "error",
            "message": "Unknown command: 'suppress_feature'",
        }
        server.execute_tool("suppress_feature", {})

        assert "edit_feature" in server.blocklisted_tools
        assert "suppress_feature" in server.blocklisted_tools

    def test_unknown_command_preserves_original_error(self):
        """The enhanced error should include the original error message."""
        mock_bridge = MagicMock()
        original_msg = "Unknown command: 'reorder_feature'"
        mock_bridge.execute.return_value = {
            "status": "error",
            "message": original_msg,
        }
        server = MCPServer(mock_bridge)

        result = server.execute_tool("reorder_feature", {"from_index": 0, "to_index": 1})

        assert result.get("original_error") == original_msg

    def test_web_tools_bypass_blocklist(self):
        """Web tools are dispatched locally and should never be blocklisted."""
        mock_bridge = MagicMock()
        server = MCPServer(mock_bridge)
        server._web_search = MagicMock()
        server._web_search.search.return_value = []

        # Even if somehow added to blocklist, web tools should still work
        # because they are dispatched before the bridge
        result = server.execute_tool("web_search", {"query": "test"})

        assert result["status"] == "success"
        assert "web_search" not in server.blocklisted_tools


# ---------------------------------------------------------------------------
# TASK-226: Tool availability validation
# ---------------------------------------------------------------------------

class TestToolAvailabilityValidation:
    """TASK-226: Verify that validate_tool_availability() cross-checks
    advertised MCP tools against the addin's registered commands."""

    def test_validate_detects_unavailable_tools(self):
        """Tools not in addin command list are flagged as unavailable."""
        mock_bridge = MagicMock()
        # Addin has most tools but NOT the timeline editing tools
        addin_commands = [
            "ping", "list_commands", "get_document_info", "create_cylinder",
            "create_box", "create_sphere", "get_body_list", "take_screenshot",
            "execute_script", "undo", "save_document", "save_document_as",
            "create_sketch", "add_sketch_line", "add_sketch_circle",
            "add_sketch_rectangle", "add_sketch_arc",
            "extrude", "revolve", "add_fillet", "add_chamfer",
            "delete_body", "mirror_body", "create_component", "apply_material",
            "export_stl", "export_step", "export_f3d",
            "get_body_properties", "get_sketch_info", "get_face_info",
            "measure_distance", "get_component_info", "validate_design",
            "redo", "get_timeline", "set_parameter",
            "list_documents", "switch_document", "new_document", "close_document",
            # Note: edit_feature, suppress_feature, delete_feature,
            # reorder_feature are intentionally MISSING
        ]
        mock_bridge.query_available_commands.return_value = addin_commands
        server = MCPServer(mock_bridge)

        result = server.validate_tool_availability()

        assert result["status"] == "success"
        assert "edit_feature" in result["unavailable"]
        assert "suppress_feature" in result["unavailable"]
        assert "delete_feature" in result["unavailable"]
        assert "reorder_feature" in result["unavailable"]
        # These should be available
        assert "get_body_list" in result["available"]
        assert "create_cylinder" in result["available"]

    def test_validate_blocklists_unavailable_tools(self):
        """Unavailable tools should be pre-added to the blocklist."""
        mock_bridge = MagicMock()
        mock_bridge.query_available_commands.return_value = [
            "ping", "list_commands", "get_document_info",
        ]
        server = MCPServer(mock_bridge)

        server.validate_tool_availability()

        # Many tools should be blocklisted since addin only has get_document_info
        assert "create_cylinder" in server.blocklisted_tools
        assert "create_box" in server.blocklisted_tools
        # get_document_info should NOT be blocklisted
        assert "get_document_info" not in server.blocklisted_tools

    def test_validate_filters_get_available_tools(self):
        """After validation, get_available_tools should exclude unavailable tools."""
        mock_bridge = MagicMock()
        mock_bridge.query_available_commands.return_value = [
            "ping", "list_commands", "get_document_info", "get_body_list",
        ]
        server = MCPServer(mock_bridge)

        server.validate_tool_availability()
        tools = server.get_available_tools()
        tool_names = {t["name"] for t in tools}

        # Addin tools: only get_document_info and get_body_list should remain
        assert "get_document_info" in tool_names
        assert "get_body_list" in tool_names
        assert "create_cylinder" not in tool_names
        # Local tools should always be present
        assert "web_search" in tool_names
        assert "read_document" in tool_names
        assert "execute_command" in tool_names

    def test_validate_skips_when_addin_has_no_list_commands(self):
        """If addin doesn't support list_commands, validation is skipped."""
        mock_bridge = MagicMock()
        mock_bridge.query_available_commands.return_value = None
        server = MCPServer(mock_bridge)

        result = server.validate_tool_availability()

        assert result["status"] == "skipped"
        # All tools should still be returned
        assert server._addin_available_tools is None
        tools = server.get_available_tools()
        assert len(tools) == len(TOOL_DEFINITIONS)

    def test_validate_reports_addin_only_commands(self):
        """Commands in addin but not in MCP definitions are reported."""
        mock_bridge = MagicMock()
        mock_bridge.query_available_commands.return_value = [
            "ping", "list_commands", "get_document_info",
            "custom_addin_command",  # not in MCP definitions
        ]
        server = MCPServer(mock_bridge)

        result = server.validate_tool_availability()

        assert "custom_addin_command" in result["addin_only"]

    def test_validate_all_tools_available(self):
        """When all tools are available, unavailable list is empty."""
        mock_bridge = MagicMock()
        # Build a complete command list from TOOL_DEFINITIONS
        all_names = [t["name"] for t in TOOL_DEFINITIONS
                     if t["name"] not in MCPServer._LOCAL_TOOLS]
        all_names.extend(["ping", "list_commands"])
        mock_bridge.query_available_commands.return_value = all_names
        server = MCPServer(mock_bridge)

        result = server.validate_tool_availability()

        assert result["status"] == "success"
        assert result["unavailable"] == []
        assert len(server.blocklisted_tools) == 0

    def test_get_available_tools_with_groups_and_filtering(self):
        """get_available_tools with groups should also respect addin filtering."""
        mock_bridge = MagicMock()
        mock_bridge.query_available_commands.return_value = [
            "ping", "list_commands", "get_document_info", "save_document",
        ]
        server = MCPServer(mock_bridge)
        server.validate_tool_availability()

        tools = server.get_available_tools(groups=["document"])
        tool_names = {t["name"] for t in tools}

        # Only document tools that the addin supports
        assert "get_document_info" in tool_names
        assert "save_document" in tool_names
        # save_document_as is in document group but not in addin
        assert "save_document_as" not in tool_names

"""
tests/test_mcp_server.py
Unit tests for mcp/server.py -- MCPServer tool registry, dispatch, and hooks.

Verifies 27 tool definitions, category mapping, schema validity, and
tool execution through the bridge in simulation mode.
"""

import pytest
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

    def test_has_exactly_41_tools(self):
        assert len(TOOL_DEFINITIONS) == 41

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
        assert len(defs) == 41

    def test_get_tool_names(self, server):
        names = server.get_tool_names()
        assert "get_body_list" in names
        assert "create_cylinder" in names
        assert "take_screenshot" in names
        assert "delete_body" in names
        assert len(names) == 41

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

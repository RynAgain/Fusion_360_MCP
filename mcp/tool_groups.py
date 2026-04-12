"""
mcp/tool_groups.py
Tool group definitions and mode-based filtering for MCP tools.

Defines logical partitions of tool capabilities that can be combined
to create CAD operating modes.  The groups are used by ai/modes.py
to restrict which tools Claude sees in a given mode.
"""

# Tool groups: logical partitions of capabilities
TOOL_GROUPS: dict[str, list[str]] = {
    "document": [
        "get_document_info", "save_document", "list_documents",
        "switch_document", "new_document", "close_document",
    ],
    "sketch": [
        "create_sketch", "add_sketch_line", "add_sketch_circle",
        "add_sketch_rectangle", "add_sketch_arc",
    ],
    "primitives": [
        "create_cylinder", "create_box", "create_sphere",
    ],
    "features": [
        "extrude", "revolve", "add_fillet", "add_chamfer",
    ],
    "body_ops": [
        "delete_body", "mirror_body", "create_component", "apply_material",
    ],
    "query": [
        "get_body_list", "get_body_properties", "get_sketch_info",
        "get_face_info", "measure_distance", "get_component_info",
        "validate_design", "get_timeline",
    ],
    "utility": [
        "undo", "redo", "set_parameter",
    ],
    "export": [
        "export_stl", "export_step", "export_f3d",
    ],
    "vision": [
        "take_screenshot",
    ],
    "scripting": [
        "execute_script",
    ],
}


def get_tools_for_groups(groups: list[str]) -> set[str]:
    """Get the set of tool names for the specified groups."""
    tools: set[str] = set()
    for group in groups:
        tools.update(TOOL_GROUPS.get(group, []))
    return tools


def get_all_tool_names() -> set[str]:
    """Get all tool names across all groups."""
    tools: set[str] = set()
    for group_tools in TOOL_GROUPS.values():
        tools.update(group_tools)
    return tools


def filter_tool_definitions(definitions: list[dict], allowed_tools: set[str]) -> list[dict]:
    """Filter tool definitions to only include allowed tools."""
    return [d for d in definitions if d["name"] in allowed_tools]

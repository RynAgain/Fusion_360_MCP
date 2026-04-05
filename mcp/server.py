"""
mcp/server.py
MCP (Model Context Protocol) tool registry.
Defines the tools that Claude can call, validates inputs, and routes
execution to the FusionBridge.
"""

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schema definitions (Anthropic tool-use format)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_document_info",
        "description": (
            "Get information about the currently open Fusion 360 document, "
            "including its name, save path, and whether it has unsaved changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "create_cylinder",
        "description": (
            "Create a solid cylinder body in the active Fusion 360 design. "
            "Dimensions are in centimetres."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "radius": {
                    "type": "number",
                    "description": "Radius of the cylinder in centimetres.",
                },
                "height": {
                    "type": "number",
                    "description": "Height (length) of the cylinder in centimetres.",
                },
                "position": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "[x, y, z] origin of the cylinder base in centimetres.",
                },
            },
            "required": ["radius", "height"],
        },
    },
    {
        "name": "create_box",
        "description": (
            "Create a solid rectangular box body in the active Fusion 360 design. "
            "Dimensions are in centimetres."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "length": {"type": "number", "description": "Length (X) in centimetres."},
                "width": {"type": "number", "description": "Width (Y) in centimetres."},
                "height": {"type": "number", "description": "Height (Z) in centimetres."},
                "position": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "[x, y, z] origin of the box in centimetres.",
                },
            },
            "required": ["length", "width", "height"],
        },
    },
    {
        "name": "create_sphere",
        "description": (
            "Create a solid sphere body in the active Fusion 360 design. "
            "Dimensions are in centimetres."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "radius": {"type": "number", "description": "Radius of the sphere in centimetres."},
                "position": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "[x, y, z] centre of the sphere in centimetres.",
                },
            },
            "required": ["radius"],
        },
    },
    {
        "name": "undo",
        "description": "Undo the last operation in Fusion 360.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "save_document",
        "description": "Save the currently active Fusion 360 document.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

# Map tool name → human-readable category for UI display
TOOL_CATEGORIES: dict[str, str] = {
    "get_document_info": "Document",
    "create_cylinder": "Geometry",
    "create_box": "Geometry",
    "create_sphere": "Geometry",
    "undo": "Edit",
    "save_document": "Document",
}


class MCPServer:
    """
    Manages the MCP tool registry and dispatches tool calls to the FusionBridge.
    Also supports optional middleware hooks (e.g. confirmation dialogs, logging).
    """

    def __init__(self, fusion_bridge):
        self.bridge = fusion_bridge
        self._pre_hooks: list[Callable[[str, dict], bool]] = []
        self._post_hooks: list[Callable[[str, dict, dict], None]] = []

    # ------------------------------------------------------------------
    # Hook registration
    # ------------------------------------------------------------------

    def add_pre_hook(self, fn: Callable[[str, dict], bool]) -> None:
        """
        Register a pre-execution hook.
        fn(tool_name, inputs) → True to allow, False to cancel.
        """
        self._pre_hooks.append(fn)

    def add_post_hook(self, fn: Callable[[str, dict, dict], None]) -> None:
        """
        Register a post-execution hook.
        fn(tool_name, inputs, result) → None
        """
        self._post_hooks.append(fn)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a named tool with the given inputs.
        Returns a result dict with at least {"status": ..., "message": ...}.
        """
        logger.info("MCP execute_tool: %s  inputs=%s", tool_name, tool_input)

        # Pre-hooks (e.g. confirmation)
        for hook in self._pre_hooks:
            allowed = hook(tool_name, tool_input)
            if not allowed:
                return {
                    "status": "cancelled",
                    "message": f"Tool '{tool_name}' was cancelled by a pre-execution hook.",
                }

        # Dispatch to bridge
        result = self.bridge.execute(tool_name, tool_input)

        # Post-hooks (e.g. logging, UI update)
        for hook in self._post_hooks:
            try:
                hook(tool_name, tool_input, result)
            except Exception as exc:
                logger.warning("Post-hook raised: %s", exc)

        logger.info("MCP result: %s", result)
        return result

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def tool_definitions(self) -> list[dict[str, Any]]:
        """Return the list of tool schemas for the Anthropic API."""
        return TOOL_DEFINITIONS

    def get_tool_names(self) -> list[str]:
        return [t["name"] for t in TOOL_DEFINITIONS]

    def describe_tools(self) -> str:
        """Return a human-readable summary of available tools."""
        lines = ["Available MCP Tools:", "=" * 40]
        for tool in TOOL_DEFINITIONS:
            cat = TOOL_CATEGORIES.get(tool["name"], "General")
            lines.append(f"  [{cat}] {tool['name']}: {tool['description'][:80]}")
        return "\n".join(lines)

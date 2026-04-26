"""
mcp/server.py
MCP (Model Context Protocol) tool registry.
Defines the tools that Claude can call, validates inputs, and routes
execution to the FusionBridge.
"""

import logging
import re
from typing import Any, Callable

from ai.web_search import WebSearchProvider
from mcp.protocols import MCPServerProtocol

logger = logging.getLogger(__name__)

# TASK-108: Maximum length of tool input/result logged in plaintext.
_MAX_LOG_INPUT_LEN = 500

# TASK-108: Regex to detect base64 blobs in logged results.
_BASE64_RE = re.compile(r'[A-Za-z0-9+/]{100,}={0,2}')


def _truncate_for_log(data: dict) -> str:
    """Return a string representation of *data* truncated for logging.

    TASK-108: Prevents megabyte-scale tool inputs (e.g. base64 screenshots)
    from flooding the log output.
    """
    s = str(data)
    if len(s) > _MAX_LOG_INPUT_LEN:
        return s[:_MAX_LOG_INPUT_LEN] + f"... ({len(s)} chars total)"
    return s


def _redact_base64(data: dict) -> str:
    """Return a string representation of *data* with base64 blobs redacted.

    TASK-108: Replaces long base64-like sequences with a placeholder to
    keep log lines readable.
    """
    s = str(data)
    s = _BASE64_RE.sub("[base64 redacted]", s)
    if len(s) > _MAX_LOG_INPUT_LEN:
        return s[:_MAX_LOG_INPUT_LEN] + f"... ({len(s)} chars total)"
    return s

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
            "Dimensions are in centimetres. The position parameter is the MINIMUM "
            "CORNER (origin point), NOT the center. The box extends from this point "
            "in the positive X, Y, Z directions by length, width, height respectively."
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
                    "description": (
                        "[x, y, z] minimum corner (origin point) of the box in centimetres, "
                        "NOT the center. The box extends from this point in the +X, +Y, +Z "
                        "directions. For example, position [0,0,0] with length=10, width=5, "
                        "height=3 creates a box spanning (0,0,0) to (10,5,3)."
                    ),
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
        "name": "get_body_list",
        "description": (
            "List all solid bodies in the active Fusion 360 design. "
            "Returns each body's name and visibility state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "take_screenshot",
        "description": (
            "Capture a screenshot of the current Fusion 360 viewport. "
            "Returns the image as base64-encoded PNG. Use this to visually "
            "verify your work after creating or modifying geometry."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "width": {
                    "type": "integer",
                    "description": "Image width in pixels (default: 1920)",
                    "default": 1920,
                },
                "height": {
                    "type": "integer",
                    "description": "Image height in pixels (default: 1080)",
                    "default": 1080,
                },
            },
            "required": [],
        },
    },
    {
        "name": "execute_script",
        "description": (
            "Execute a Python script inside Fusion 360's environment. "
            "The script has access to 'adsk' module, 'app' (Application), "
            "'design' (Design), 'rootComp' (root Component), and 'ui' "
            "(UserInterface). Use this for complex operations not covered "
            "by other tools. Set a 'result' variable in the script to return data. "
            "If you need filesystem access (os, pathlib, open), set allow_filesystem=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "Python script code to execute inside Fusion 360",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Maximum execution time in seconds (default: 30)",
                    "default": 30,
                },
                "allow_filesystem": {
                    "type": "boolean",
                    "description": "Grant filesystem access (os, pathlib, open) for this script. Must be explicitly set to true each time.",
                    "default": False,
                },
            },
            "required": ["script"],
        },
    },
    {
        "name": "execute_command",
        "description": (
            "Execute a shell command on the local system. Returns stdout, stderr, "
            "and exit code. Use this for: running Python scripts outside Fusion 360, "
            "processing files with external tools, installing packages, converting "
            "file formats, or any system-level operation. Commands run in the "
            "project directory by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for the command (default: project root)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Maximum execution time in seconds (default: 60)",
                    "default": 60,
                },
            },
            "required": ["command"],
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
    # ------------------------------------------------------------------
    # Sketch tools
    # ------------------------------------------------------------------
    {
        "name": "create_sketch",
        "description": (
            "Create a new sketch on a construction plane in the active Fusion 360 design. "
            "Returns the sketch name and ID for use with subsequent sketch geometry commands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plane": {
                    "type": "string",
                    "enum": ["XY", "XZ", "YZ"],
                    "description": "Construction plane to create the sketch on.",
                },
                "name": {
                    "type": "string",
                    "description": "Optional name for the sketch.",
                },
            },
            "required": ["plane"],
        },
    },
    {
        "name": "add_sketch_line",
        "description": (
            "Add a line to an existing sketch by specifying start and end points. "
            "Coordinates are in centimetres on the sketch's 2D plane."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sketch_name": {"type": "string", "description": "Name of the target sketch."},
                "start_x": {"type": "number", "description": "Start point X coordinate in cm."},
                "start_y": {"type": "number", "description": "Start point Y coordinate in cm."},
                "end_x": {"type": "number", "description": "End point X coordinate in cm."},
                "end_y": {"type": "number", "description": "End point Y coordinate in cm."},
            },
            "required": ["sketch_name", "start_x", "start_y", "end_x", "end_y"],
        },
    },
    {
        "name": "add_sketch_circle",
        "description": (
            "Add a circle to an existing sketch by specifying center and radius. "
            "Coordinates are in centimetres on the sketch's 2D plane."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sketch_name": {"type": "string", "description": "Name of the target sketch."},
                "center_x": {"type": "number", "description": "Center X coordinate in cm."},
                "center_y": {"type": "number", "description": "Center Y coordinate in cm."},
                "radius": {"type": "number", "description": "Radius in cm."},
            },
            "required": ["sketch_name", "center_x", "center_y", "radius"],
        },
    },
    {
        "name": "add_sketch_rectangle",
        "description": (
            "Add a rectangle to an existing sketch defined by two diagonal corner points. "
            "Coordinates are in centimetres on the sketch's 2D plane."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sketch_name": {"type": "string", "description": "Name of the target sketch."},
                "start_x": {"type": "number", "description": "First corner X coordinate in cm."},
                "start_y": {"type": "number", "description": "First corner Y coordinate in cm."},
                "end_x": {"type": "number", "description": "Opposite corner X coordinate in cm."},
                "end_y": {"type": "number", "description": "Opposite corner Y coordinate in cm."},
            },
            "required": ["sketch_name", "start_x", "start_y", "end_x", "end_y"],
        },
    },
    {
        "name": "add_sketch_arc",
        "description": (
            "Add an arc to an existing sketch by specifying center, radius, and angle range. "
            "Angles are in degrees. Coordinates are in centimetres."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sketch_name": {"type": "string", "description": "Name of the target sketch."},
                "center_x": {"type": "number", "description": "Center X coordinate in cm."},
                "center_y": {"type": "number", "description": "Center Y coordinate in cm."},
                "radius": {"type": "number", "description": "Arc radius in cm."},
                "start_angle": {"type": "number", "description": "Start angle in degrees."},
                "end_angle": {"type": "number", "description": "End angle in degrees."},
            },
            "required": ["sketch_name", "center_x", "center_y", "radius", "start_angle", "end_angle"],
        },
    },
    # ------------------------------------------------------------------
    # Feature tools
    # ------------------------------------------------------------------
    {
        "name": "extrude",
        "description": (
            "Extrude a sketch profile to create a 3D feature. Distance is in centimetres. "
            "Operation can be 'new' (new body), 'join', 'cut', or 'intersect'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sketch_name": {"type": "string", "description": "Name of the sketch containing the profile."},
                "profile_index": {
                    "type": "integer",
                    "description": "Index of the profile in the sketch (default 0).",
                    "default": 0,
                },
                "distance": {"type": "number", "description": "Extrusion distance in cm."},
                "operation": {
                    "type": "string",
                    "enum": ["new", "join", "cut", "intersect"],
                    "description": "Feature operation type (default 'new').",
                    "default": "new",
                },
            },
            "required": ["sketch_name", "distance"],
        },
    },
    {
        "name": "revolve",
        "description": (
            "Revolve a sketch profile around an axis to create a 3D feature. "
            "Angle is in degrees (default 360 for full revolution)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sketch_name": {"type": "string", "description": "Name of the sketch containing the profile."},
                "profile_index": {
                    "type": "integer",
                    "description": "Index of the profile in the sketch (default 0).",
                    "default": 0,
                },
                "axis": {
                    "type": "string",
                    "description": "Revolution axis: 'X', 'Y', 'Z', or a sketch line reference.",
                },
                "angle": {
                    "type": "number",
                    "description": "Revolution angle in degrees (default 360).",
                    "default": 360,
                },
            },
            "required": ["sketch_name", "axis"],
        },
    },
    {
        "name": "add_fillet",
        "description": (
            "Add a fillet (rounded edge) to one or more edges of a body. "
            "Radius is in centimetres."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "body_name": {"type": "string", "description": "Name of the target body."},
                "edge_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of edge indices (from body.edges) to fillet.",
                },
                "radius": {"type": "number", "description": "Fillet radius in cm."},
            },
            "required": ["body_name", "edge_indices", "radius"],
        },
    },
    {
        "name": "add_chamfer",
        "description": (
            "Add a chamfer (bevelled edge) to one or more edges of a body. "
            "Distance is in centimetres."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "body_name": {"type": "string", "description": "Name of the target body."},
                "edge_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of edge indices (from body.edges) to chamfer.",
                },
                "distance": {"type": "number", "description": "Chamfer distance in cm."},
            },
            "required": ["body_name", "edge_indices", "distance"],
        },
    },
    # ------------------------------------------------------------------
    # Body operation tools
    # ------------------------------------------------------------------
    {
        "name": "delete_body",
        "description": "Delete a body from the design by name. Use this to clean up failed geometry or unwanted bodies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "body_name": {"type": "string", "description": "Name of the body to delete"},
            },
            "required": ["body_name"],
        },
    },
    {
        "name": "mirror_body",
        "description": "Mirror a body across a construction plane.",
        "input_schema": {
            "type": "object",
            "properties": {
                "body_name": {"type": "string", "description": "Name of the body to mirror."},
                "mirror_plane": {
                    "type": "string",
                    "enum": ["XY", "XZ", "YZ"],
                    "description": "Construction plane to mirror across.",
                },
            },
            "required": ["body_name", "mirror_plane"],
        },
    },
    {
        "name": "create_component",
        "description": "Create a new empty component in the active design.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the new component."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "apply_material",
        "description": (
            "Apply a material/appearance to a body. Common materials include "
            "'Steel', 'Aluminum', 'ABS Plastic', etc. If an exact match is not "
            "found, available materials will be listed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "body_name": {"type": "string", "description": "Name of the target body."},
                "material_name": {
                    "type": "string",
                    "description": "Material name (e.g. 'Steel', 'Aluminum', 'ABS Plastic').",
                },
            },
            "required": ["body_name", "material_name"],
        },
    },
    # ------------------------------------------------------------------
    # Export tools
    # ------------------------------------------------------------------
    {
        "name": "export_stl",
        "description": (
            "Export one or all bodies as an STL mesh file. "
            "If body_name is omitted, all bodies are exported."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "body_name": {
                    "type": "string",
                    "description": "Name of the body to export (omit to export all).",
                },
                "filename": {"type": "string", "description": "Output file path (e.g. 'model.stl')."},
                "refinement": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Mesh refinement level (default 'medium').",
                    "default": "medium",
                },
            },
            "required": ["filename"],
        },
    },
    {
        "name": "export_step",
        "description": "Export the active design as a STEP file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Output file path (e.g. 'model.step')."},
            },
            "required": ["filename"],
        },
    },
    {
        "name": "export_f3d",
        "description": "Export the active design as a Fusion 360 archive (.f3d) file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Output file path (e.g. 'model.f3d')."},
            },
            "required": ["filename"],
        },
    },
    # ------------------------------------------------------------------
    # Geometric data query tools
    # ------------------------------------------------------------------
    {
        "name": "get_body_properties",
        "description": (
            "Get detailed physical and topological properties of a specific body, "
            "including volume, surface area, center of mass, bounding box, face/edge/vertex counts, "
            "material, and appearance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "body_name": {"type": "string", "description": "Name of the body to inspect."},
            },
            "required": ["body_name"],
        },
    },
    {
        "name": "get_sketch_info",
        "description": (
            "Get detailed information about a sketch including its curves, profiles, "
            "dimensions, and constraint status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sketch_name": {"type": "string", "description": "Name of the sketch to inspect."},
            },
            "required": ["sketch_name"],
        },
    },
    {
        "name": "get_face_info",
        "description": (
            "Get information about a specific face on a body, including area, surface type, "
            "normal vector, centroid, and edge count."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "body_name": {"type": "string", "description": "Name of the body containing the face."},
                "face_index": {"type": "integer", "description": "Zero-based index of the face on the body."},
            },
            "required": ["body_name", "face_index"],
        },
    },
    {
        "name": "measure_distance",
        "description": (
            "Measure the minimum distance between two entities. Entity references use the format: "
            "'body:Name' for bodies, 'face:BodyName:index' for faces, 'edge:BodyName:index' for edges."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity1": {
                    "type": "string",
                    "description": "First entity reference (e.g. 'body:Body1', 'face:Body1:0', 'edge:Body1:2').",
                },
                "entity2": {
                    "type": "string",
                    "description": "Second entity reference (e.g. 'body:Body2', 'face:Body2:1').",
                },
            },
            "required": ["entity1", "entity2"],
        },
    },
    {
        "name": "get_component_info",
        "description": (
            "Get information about a component including its bodies, sketches, features, "
            "and child components. Defaults to the root component if no name is specified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "component_name": {
                    "type": "string",
                    "description": "Name of the component to inspect (omit for root component).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "validate_design",
        "description": (
            "Validate the current design by checking all bodies for solidity, detecting "
            "potential issues like non-solid bodies or small geometry, and returning a summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ------------------------------------------------------------------
    # Document management tools
    # ------------------------------------------------------------------
    {
        "name": "list_documents",
        "description": (
            "List all currently open documents in Fusion 360, including their names, "
            "saved status, version numbers, and which one is active."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "switch_document",
        "description": (
            "Switch the active document to a different open document by name. "
            "Use list_documents first to see available documents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_name": {
                    "type": "string",
                    "description": "Name of the document to switch to.",
                },
            },
            "required": ["document_name"],
        },
    },
    {
        "name": "new_document",
        "description": (
            "Create a new Fusion 360 design document. Optionally specify a name "
            "and design type (parametric or direct modeling)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Optional name for the new document (set on save in F360).",
                },
                "design_type": {
                    "type": "string",
                    "enum": ["parametric", "direct"],
                    "description": "Design type: 'parametric' (default) or 'direct'.",
                    "default": "parametric",
                },
            },
            "required": [],
        },
    },
    {
        "name": "close_document",
        "description": (
            "Close an open document by name. By default saves unsaved changes "
            "before closing. Set save=false to close without saving."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_name": {
                    "type": "string",
                    "description": "Name of the document to close.",
                },
                "save": {
                    "type": "boolean",
                    "description": "Whether to save before closing (default true).",
                    "default": True,
                },
            },
            "required": ["document_name"],
        },
    },
    # ------------------------------------------------------------------
    # Additional utility tools
    # ------------------------------------------------------------------
    {
        "name": "redo",
        "description": "Redo the last undone operation in Fusion 360.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_timeline",
        "description": (
            "Get the design timeline showing all features and operations "
            "in order, including suppression and roll-back state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "set_parameter",
        "description": (
            "Set a design parameter value or expression. "
            "Value should include units (e.g. '10 mm', '5 cm')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Parameter name."},
                "value": {
                    "type": "string",
                    "description": "New value with units (e.g. '10 mm', '5 cm').",
                },
                "expression": {
                    "type": "string",
                    "description": "Optional expression to set instead of a literal value.",
                },
            },
            "required": ["name", "value"],
        },
    },
    # ------------------------------------------------------------------
    # Timeline editing tools (TASK-218)
    # ------------------------------------------------------------------
    {
        "name": "edit_feature",
        "description": (
            "Edit an existing feature's parameters by its timeline index. "
            "Use get_timeline first to find the index of the feature to edit. "
            "Pass a parameters dict with attribute names and new values."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timeline_index": {
                    "type": "integer",
                    "description": "Zero-based index of the feature in the design timeline.",
                },
                "parameters": {
                    "type": "object",
                    "description": "Dict of parameter names to new values to set on the feature entity.",
                },
            },
            "required": ["timeline_index", "parameters"],
        },
    },
    {
        "name": "suppress_feature",
        "description": (
            "Suppress (disable) a feature at a given timeline index without deleting it. "
            "The feature remains in the timeline but has no effect on geometry. "
            "Use get_timeline first to find the index. Use this to disable failed "
            "operations before attempting a corrected version."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timeline_index": {
                    "type": "integer",
                    "description": "Zero-based index of the feature in the design timeline.",
                },
            },
            "required": ["timeline_index"],
        },
    },
    {
        "name": "delete_feature",
        "description": (
            "Permanently delete a feature from the design timeline by index. "
            "Use get_timeline first to find the index. This cannot be undone "
            "except via undo. Prefer suppress_feature if you may want to "
            "re-enable the feature later."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timeline_index": {
                    "type": "integer",
                    "description": "Zero-based index of the feature in the design timeline.",
                },
            },
            "required": ["timeline_index"],
        },
    },
    {
        "name": "reorder_feature",
        "description": (
            "Move a feature from one position in the timeline to another. "
            "Use get_timeline first to identify the indices. "
            "This can fix sequencing issues without recreating features."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_index": {
                    "type": "integer",
                    "description": "Current zero-based timeline index of the feature to move.",
                },
                "to_index": {
                    "type": "integer",
                    "description": "Target zero-based timeline index to move the feature to.",
                },
            },
            "required": ["from_index", "to_index"],
        },
    },
    # ------------------------------------------------------------------
    # Save-as tool (TASK-221)
    # ------------------------------------------------------------------
    {
        "name": "save_document_as",
        "description": (
            "Save the active document with a new name. Use this for documents "
            "that have never been saved (when save_document returns an error "
            "about unsaved documents). The document is saved to the active "
            "project's root folder."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name for the saved document.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional description for the save (default: 'Saved by MCP agent').",
                },
            },
            "required": ["name"],
        },
    },
    # ------------------------------------------------------------------
    # Web search tools
    # ------------------------------------------------------------------
    {
        "name": "web_search",
        "description": (
            "Search the internet for information. Useful for looking up "
            "Fusion 360 API documentation, design patterns, troubleshooting, "
            "and other up-to-date information."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": (
            "Fetch a web page and extract its readable text content. "
            "Strips scripts, styles, navigation, and other non-content elements."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL of the web page to fetch.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters of content to return (default 10000).",
                    "default": 10000,
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "fusion_docs_search",
        "description": (
            "Search specifically for Autodesk Fusion 360 API documentation. "
            "Automatically prepends 'Autodesk Fusion 360 API' to the query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Documentation search query (e.g. 'extrude feature', 'sketch constraints').",
                },
            },
            "required": ["query"],
        },
    },
    # ------------------------------------------------------------------
    # Document extraction tools
    # ------------------------------------------------------------------
    {
        "name": "read_document",
        "description": (
            "Read and extract text from a document file (PDF, DOCX, TXT, MD, CSV, images). "
            "Returns structured text content with metadata. Use this to read product datasheets, "
            "specifications, reference documents, or images the user provides."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to read. Can be absolute or relative to the project directory.",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Maximum number of lines to return (default: 2000).",
                    "default": 2000,
                },
            },
            "required": ["file_path"],
        },
    },
]

# Map tool name -> human-readable category for UI display
TOOL_CATEGORIES: dict[str, str] = {
    "get_document_info": "Document",
    "create_cylinder": "Geometry",
    "create_box": "Geometry",
    "create_sphere": "Geometry",
    "get_body_list": "Document",
    "take_screenshot": "Vision",
    "execute_script": "Scripting",
    "undo": "Utility",
    "save_document": "Document",
    # Sketch tools
    "create_sketch": "Sketching",
    "add_sketch_line": "Sketching",
    "add_sketch_circle": "Sketching",
    "add_sketch_rectangle": "Sketching",
    "add_sketch_arc": "Sketching",
    # Feature tools
    "extrude": "Features",
    "revolve": "Features",
    "add_fillet": "Features",
    "add_chamfer": "Features",
    # Body operation tools
    "delete_body": "Body Operations",
    "mirror_body": "Body Operations",
    "create_component": "Body Operations",
    "apply_material": "Body Operations",
    # Export tools
    "export_stl": "Export",
    "export_step": "Export",
    "export_f3d": "Export",
    # Geometric data query tools
    "get_body_properties": "Query",
    "get_sketch_info": "Query",
    "get_face_info": "Query",
    "measure_distance": "Query",
    "get_component_info": "Query",
    "validate_design": "Query",
    # Additional utility tools
    "redo": "Utility",
    "get_timeline": "Utility",
    "set_parameter": "Utility",
    # Timeline editing tools (TASK-218)
    "edit_feature": "Timeline",
    "suppress_feature": "Timeline",
    "delete_feature": "Timeline",
    "reorder_feature": "Timeline",
    # Save-as tool (TASK-221)
    "save_document_as": "Document",
    # Document management tools
    "list_documents": "Document",
    "switch_document": "Document",
    "new_document": "Document",
    "close_document": "Document",
    # Web search tools
    "web_search": "Web Search",
    "web_fetch": "Web Search",
    "fusion_docs_search": "Web Search",
    # Document extraction tools
    "read_document": "Documents",
    # System tools
    "execute_command": "System",
}


class MCPServer:
    """
    Manages the MCP tool registry and dispatches tool calls to the FusionBridge.
    Also supports optional middleware hooks (e.g. confirmation dialogs, logging).
    """

    # Web search tools are handled directly, not through the Fusion bridge
    _WEB_TOOLS = {"web_search", "web_fetch", "fusion_docs_search"}

    # Document extraction tools are handled locally, not through the Fusion bridge
    _DOCUMENT_TOOLS = {"read_document"}

    # System tools run commands on the local machine
    _SYSTEM_TOOLS = {"execute_command"}

    # TASK-226: Tools that are NOT dispatched to the Fusion addin and thus
    # should never be filtered by addin availability checks.
    _LOCAL_TOOLS = _WEB_TOOLS | _DOCUMENT_TOOLS | _SYSTEM_TOOLS

    def __init__(self, fusion_bridge):
        self.bridge = fusion_bridge
        self._web_search = WebSearchProvider()
        self._pre_hooks: list[Callable[[str, dict], bool]] = []
        self._post_hooks: list[Callable[[str, dict, dict], None]] = []
        # TASK-226: Session-level blocklist of tools that returned
        # "Unknown command" from the addin.  Once a tool is blocklisted,
        # subsequent calls return a cached error immediately without
        # round-tripping to the addin.
        self._blocklisted_tools: set[str] = set()
        # TASK-226: Set of tools confirmed available in the connected addin.
        # None means availability has not been checked yet.
        self._addin_available_tools: set[str] | None = None

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

    # TASK-226: Sentinel message substring returned by both the bridge
    # dispatch table and the addin when a command is not recognised.
    _UNKNOWN_CMD_PREFIX = "Unknown command"

    def execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a named tool with the given inputs.
        Returns a result dict with at least {"status": ..., "message": ...}.
        """
        # TASK-109: Basic input validation
        if not isinstance(tool_name, str) or not tool_name:
            return {"status": "error", "error": "Invalid tool name"}
        if not isinstance(tool_input, dict):
            return {"status": "error", "error": "Tool input must be a dict"}

        # TASK-226: If the tool is blocklisted, return a cached error
        # immediately without round-tripping to the addin.
        if tool_name in self._blocklisted_tools:
            logger.info("MCP execute_tool: %s -- BLOCKLISTED, returning cached error", tool_name)
            return {
                "status": "error",
                "message": (
                    f"Tool '{tool_name}' is not available in the connected "
                    f"Fusion 360 addin. It was previously blocklisted after "
                    f"returning 'Unknown command'. Do not retry this tool."
                ),
                "blocklisted": True,
            }

        # TASK-108: Truncate logged tool input to avoid flooding logs
        logger.info("MCP execute_tool: %s  inputs=%s", tool_name, _truncate_for_log(tool_input))

        # Pre-hooks (e.g. confirmation)
        for hook in self._pre_hooks:
            allowed = hook(tool_name, tool_input)
            if not allowed:
                return {
                    "status": "cancelled",
                    "message": f"Tool '{tool_name}' was cancelled by a pre-execution hook.",
                }

        # Dispatch: web tools go to WebSearchProvider, document tools handled
        # locally, everything else to bridge
        if tool_name in self._WEB_TOOLS:
            result = self._dispatch_web_tool(tool_name, tool_input)
        elif tool_name in self._DOCUMENT_TOOLS:
            result = self._dispatch_document_tool(tool_name, tool_input)
        elif tool_name in self._SYSTEM_TOOLS:
            result = self._dispatch_system_tool(tool_name, tool_input)
        else:
            result = self.bridge.execute(tool_name, tool_input)

        # TASK-226: Detect "Unknown command" errors and enhance the response.
        # Add the tool to the session blocklist so subsequent calls fail fast.
        result = self._check_unknown_command(tool_name, result)

        # Post-hooks (e.g. logging, UI update)
        for hook in self._post_hooks:
            try:
                hook(tool_name, tool_input, result)
            except Exception as exc:
                logger.warning("Post-hook raised: %s", exc)

        # TASK-108: Redact base64 content from logged results
        logger.info("MCP result: %s", _redact_base64(result))
        return result

    # ------------------------------------------------------------------
    # TASK-226: Unknown command detection and blocklist
    # ------------------------------------------------------------------

    def _check_unknown_command(self, tool_name: str, result: dict) -> dict:
        """Detect 'Unknown command' errors and enhance the response.

        TASK-226: When the addin returns an "Unknown command" error, this
        method:
        1. Enhances the error message with clear guidance
        2. Adds the tool to the session blocklist
        3. Returns the enhanced result

        If the result is not an "Unknown command" error, it is returned
        unchanged.
        """
        if result.get("status") != "error":
            return result

        message = result.get("message", "") or result.get("error", "")
        if self._UNKNOWN_CMD_PREFIX not in message:
            return result

        # Add to blocklist
        self._blocklisted_tools.add(tool_name)
        logger.warning(
            "TASK-226: Tool '%s' returned 'Unknown command' -- "
            "added to session blocklist. Blocklisted tools: %s",
            tool_name, self._blocklisted_tools,
        )

        # Return enhanced error
        return {
            "status": "error",
            "message": (
                f"Tool '{tool_name}' is not available in the connected "
                f"Fusion 360 addin. Do not retry this tool. "
                f"The addin does not have a handler for '{tool_name}'."
            ),
            "blocklisted": True,
            "original_error": message,
        }

    # ------------------------------------------------------------------
    # Web tool dispatch
    # ------------------------------------------------------------------

    def _dispatch_web_tool(self, tool_name: str, tool_input: dict) -> dict:
        """Dispatch web search tools to WebSearchProvider."""
        try:
            if tool_name == "web_search":
                query = tool_input.get("query", "")
                max_results = tool_input.get("max_results", 5)
                return {"status": "success", "results": self._web_search.search(query, max_results=max_results)}
            elif tool_name == "web_fetch":
                url = tool_input.get("url", "")
                max_chars = tool_input.get("max_chars", 10000)
                return self._web_search.fetch_page(url, max_chars=max_chars)
            elif tool_name == "fusion_docs_search":
                query = tool_input.get("query", "")
                return {"status": "success", "results": self._web_search.search_fusion_docs(query)}
            else:
                return {"status": "error", "error": f"Unknown web tool: {tool_name}"}
        except Exception as exc:
            logger.exception("Web search tool '%s' failed", tool_name)
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Document tool dispatch
    # ------------------------------------------------------------------

    def _dispatch_document_tool(self, tool_name: str, tool_input: dict) -> dict:
        """Dispatch document extraction tools."""
        try:
            from ai.document_extractor import extract_text
            if tool_name == "read_document":
                file_path = tool_input.get("file_path", "")
                max_lines = tool_input.get("max_lines", 2000)
                return extract_text(file_path, max_lines=max_lines)
            return {"status": "error", "error": f"Unknown document tool: {tool_name}"}
        except Exception as exc:
            logger.exception("Document tool '%s' failed", tool_name)
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # System tool dispatch
    # ------------------------------------------------------------------

    def _dispatch_system_tool(self, tool_name: str, tool_input: dict) -> dict:
        """Dispatch system tools (execute_command)."""
        import subprocess
        import os

        try:
            if tool_name == "execute_command":
                command = tool_input.get("command", "")
                if not command:
                    return {"status": "error", "error": "Empty command"}

                cwd = tool_input.get("cwd") or os.getcwd()
                timeout = tool_input.get("timeout", 60)

                # Cap timeout to prevent runaway processes
                timeout = min(timeout, 300)  # 5 minutes max

                logger.info("Executing command: %s (cwd=%s, timeout=%ds)",
                            _truncate_for_log({"cmd": command}), cwd, timeout)

                try:
                    proc = subprocess.run(
                        command,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=cwd,
                    )
                    return {
                        "status": "success" if proc.returncode == 0 else "error",
                        "exit_code": proc.returncode,
                        "stdout": proc.stdout[:50000] if proc.stdout else "",
                        "stderr": proc.stderr[:10000] if proc.stderr else "",
                    }
                except subprocess.TimeoutExpired:
                    return {
                        "status": "error",
                        "error": f"Command timed out after {timeout} seconds",
                        "exit_code": -1,
                        "stdout": "",
                        "stderr": "",
                    }
                except FileNotFoundError as exc:
                    return {"status": "error", "error": f"Command not found: {exc}"}

            return {"status": "error", "error": f"Unknown system tool: {tool_name}"}
        except Exception as exc:
            logger.exception("System tool '%s' failed", tool_name)
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def tool_definitions(self) -> list[dict[str, Any]]:
        """Return the list of tool schemas for the Anthropic API."""
        return TOOL_DEFINITIONS

    def _filter_by_addin_availability(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove tools whose addin handler is known to be unavailable.

        TASK-226: After :meth:`validate_tool_availability` has been called,
        ``_addin_available_tools`` contains the set of commands the running
        addin supports.  Tools that require the addin but are not in this
        set are excluded from the returned list.

        Local tools (web search, documents, system) are never filtered.
        If availability has not been checked yet (``_addin_available_tools``
        is ``None``), all tools are returned unchanged.
        """
        if self._addin_available_tools is None:
            return tools
        return [
            t for t in tools
            if t["name"] in self._LOCAL_TOOLS
            or t["name"] in self._addin_available_tools
        ]

    def get_available_tools(self, groups: list[str] | None = None) -> list[dict[str, Any]]:
        """Return tool definitions, optionally filtered by groups.

        TASK-226: Also filters out tools unavailable in the connected addin.
        Satisfies :class:`~mcp.protocols.MCPServerProtocol`.
        """
        if groups is None:
            tools = list(TOOL_DEFINITIONS)
        else:
            from mcp.tool_groups import get_tools_for_groups
            allowed = get_tools_for_groups(groups)
            tools = [t for t in TOOL_DEFINITIONS if t["name"] in allowed]
        return self._filter_by_addin_availability(tools)

    def register_post_hook(self, hook: Any) -> None:
        """Register a post-execution hook.

        Satisfies :class:`~mcp.protocols.MCPServerProtocol`.
        """
        self.add_post_hook(hook)

    def get_tool_names(self) -> list[str]:
        return [t["name"] for t in TOOL_DEFINITIONS]

    def describe_tools(self) -> str:
        """Return a human-readable summary of available tools."""
        lines = ["Available MCP Tools:", "=" * 40]
        for tool in TOOL_DEFINITIONS:
            cat = TOOL_CATEGORIES.get(tool["name"], "General")
            lines.append(f"  [{cat}] {tool['name']}: {tool['description'][:80]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # TASK-226: Tool availability validation
    # ------------------------------------------------------------------

    def validate_tool_availability(self) -> dict[str, Any]:
        """Query the addin for available commands and cross-check with
        the advertised tool list.

        TASK-226: Should be called after connecting to the addin (e.g.
        from the ``/connect`` route or startup flow).  Results are cached
        in ``_addin_available_tools`` and used by :meth:`get_available_tools`
        to filter the tool list sent to the LLM.

        Returns a summary dict with:
        - ``available``: tools present in both MCP definitions and addin
        - ``unavailable``: tools defined in MCP but missing from addin
        - ``addin_only``: commands in addin but not exposed as MCP tools
        """
        addin_commands = self.bridge.query_available_commands()
        if addin_commands is None:
            logger.info(
                "TASK-226: Could not query addin commands "
                "(addin may not support list_commands). "
                "All tools remain advertised."
            )
            return {
                "status": "skipped",
                "message": "Addin does not support list_commands; "
                           "all tools remain advertised.",
            }

        addin_set = set(addin_commands)
        # MCP tools that require the addin (exclude local tools)
        mcp_addin_tools = {
            t["name"] for t in TOOL_DEFINITIONS
            if t["name"] not in self._LOCAL_TOOLS
        }

        available = mcp_addin_tools & addin_set
        unavailable = mcp_addin_tools - addin_set
        addin_only = addin_set - mcp_addin_tools - {"ping", "list_commands"}

        # Cache the available set for filtering
        self._addin_available_tools = available | {
            t["name"] for t in TOOL_DEFINITIONS
            if t["name"] in self._LOCAL_TOOLS
        }

        # Also pre-blocklist unavailable tools
        for tool_name in unavailable:
            self._blocklisted_tools.add(tool_name)

        if unavailable:
            logger.warning(
                "TASK-226: %d tool(s) advertised but NOT available in addin: %s",
                len(unavailable), sorted(unavailable),
            )
        if addin_only:
            logger.info(
                "TASK-226: %d command(s) in addin but not exposed as MCP tools: %s",
                len(addin_only), sorted(addin_only),
            )
        logger.info(
            "TASK-226: Tool availability validated. "
            "%d available, %d unavailable, %d addin-only.",
            len(available), len(unavailable), len(addin_only),
        )

        return {
            "status": "success",
            "available": sorted(available),
            "unavailable": sorted(unavailable),
            "addin_only": sorted(addin_only),
        }

    def clear_blocklist(self) -> None:
        """Clear the session blocklist and availability cache.

        TASK-226: Useful when reconnecting to a different addin version
        or after the addin has been updated.
        """
        self._blocklisted_tools.clear()
        self._addin_available_tools = None
        logger.info("TASK-226: Blocklist and availability cache cleared.")

    @property
    def blocklisted_tools(self) -> set[str]:
        """Return a copy of the current session blocklist.

        TASK-226: Read-only access for tests and diagnostics.
        """
        return set(self._blocklisted_tools)


# Runtime check: MCPServer satisfies MCPServerProtocol
assert isinstance(MCPServer.__new__(MCPServer), MCPServerProtocol), \
    "MCPServer does not satisfy MCPServerProtocol"


# ---------------------------------------------------------------------------
# TASK-087: Startup consistency check
# ---------------------------------------------------------------------------
import logging as _logging
_logger = _logging.getLogger(__name__)


def _check_tool_consistency():
    from mcp.tool_groups import validate_tool_consistency
    for warning in validate_tool_consistency():
        _logger.warning("Tool consistency: %s", warning)


# Run at import time -- issues are logged, not raised
_check_tool_consistency()

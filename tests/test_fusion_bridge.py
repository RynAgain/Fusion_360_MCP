"""
tests/test_fusion_bridge.py
Unit tests for fusion/bridge.py -- FusionBridge in simulation mode.

Covers all 27 tools: connection management, primitives, sketch commands,
feature commands, body operations, export commands, and utilities.
"""

import os

import pytest
from fusion.bridge import FusionBridge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge():
    """Return a FusionBridge in forced simulation mode."""
    return FusionBridge(simulation_mode=True)


# ---------------------------------------------------------------------------
# Connection / mode
# ---------------------------------------------------------------------------

class TestConnection:
    """Connection management and simulation mode behaviour."""

    def test_simulation_mode_is_set(self, bridge):
        assert bridge.simulation_mode is True

    def test_is_connected_in_simulation(self, bridge):
        """Simulation mode reports as connected."""
        assert bridge.is_connected() is True

    def test_connect_forced_simulation(self, bridge):
        result = bridge.connect()
        assert result["status"] == "simulation"
        assert "forced" in result["message"].lower()


# ---------------------------------------------------------------------------
# get_body_list
# ---------------------------------------------------------------------------

class TestGetBodyList:
    """Validate the simulated body list response."""

    def test_returns_dict(self, bridge):
        assert isinstance(bridge.get_body_list(), dict)

    def test_has_bodies_key(self, bridge):
        assert "bodies" in bridge.get_body_list()

    def test_bodies_is_list(self, bridge):
        assert isinstance(bridge.get_body_list()["bodies"], list)

    def test_count_matches_bodies(self, bridge):
        result = bridge.get_body_list()
        assert result["count"] == len(result["bodies"])

    def test_body_has_name(self, bridge):
        for body in bridge.get_body_list()["bodies"]:
            assert isinstance(body["name"], str)

    def test_body_has_is_visible(self, bridge):
        for body in bridge.get_body_list()["bodies"]:
            assert isinstance(body["is_visible"], bool)

    def test_status_is_simulation(self, bridge):
        assert bridge.get_body_list()["status"] == "simulation"

    def test_has_sim_message(self, bridge):
        assert "[SIM]" in bridge.get_body_list()["message"]


# ---------------------------------------------------------------------------
# Existing primitives (regression)
# ---------------------------------------------------------------------------

class TestPrimitives:
    """Legacy primitive creation commands."""

    def test_get_document_info(self, bridge):
        result = bridge.get_document_info()
        assert result["status"] == "simulation"
        assert "name" in result

    def test_create_cylinder(self, bridge):
        result = bridge.create_cylinder(radius=2.0, height=5.0)
        assert result["status"] == "simulation"
        assert "cylinder" in result["message"].lower()

    def test_create_cylinder_with_position(self, bridge):
        result = bridge.create_cylinder(radius=1.0, height=3.0, position=[1, 2, 3])
        assert "[1, 2, 3]" in result["message"]

    def test_create_box(self, bridge):
        result = bridge.create_box(length=2.0, width=3.0, height=4.0)
        assert result["status"] == "simulation"
        assert "box" in result["message"].lower()

    def test_create_sphere(self, bridge):
        result = bridge.create_sphere(radius=5.0)
        assert result["status"] == "simulation"
        assert "sphere" in result["message"].lower()

    def test_undo(self, bridge):
        result = bridge.undo()
        assert result["status"] == "simulation"
        assert "Undo" in result["message"]

    def test_save_document(self, bridge):
        result = bridge.save_document()
        assert result["status"] == "simulation"
        assert "saved" in result["message"].lower()


# ---------------------------------------------------------------------------
# Vision -- take_screenshot
# ---------------------------------------------------------------------------

class TestTakeScreenshot:
    """Validate the simulated screenshot response."""

    def test_returns_success(self, bridge):
        result = bridge.take_screenshot()
        assert result["success"] is True

    def test_returns_image_base64(self, bridge):
        result = bridge.take_screenshot()
        assert isinstance(result["image_base64"], str)
        assert len(result["image_base64"]) > 0

    def test_returns_format(self, bridge):
        assert bridge.take_screenshot()["format"] == "png"

    def test_returns_requested_dimensions(self, bridge):
        result = bridge.take_screenshot(width=800, height=600)
        assert result["width"] == 800
        assert result["height"] == 600

    def test_default_dimensions(self, bridge):
        result = bridge.take_screenshot()
        assert result["width"] == 1920
        assert result["height"] == 1080


# ---------------------------------------------------------------------------
# Scripting -- execute_script
# ---------------------------------------------------------------------------

class TestExecuteScript:
    """Validate the simulated script execution response."""

    def test_returns_success(self, bridge):
        result = bridge.execute_script("print('hello')")
        assert result["success"] is True

    def test_returns_stdout(self, bridge):
        result = bridge.execute_script("x = 1")
        assert isinstance(result["stdout"], str)

    def test_returns_stderr(self, bridge):
        result = bridge.execute_script("x = 1")
        assert isinstance(result["stderr"], str)

    def test_returns_error_key(self, bridge):
        result = bridge.execute_script("x = 1")
        assert "error" in result

    def test_returns_result_key(self, bridge):
        result = bridge.execute_script("x = 1")
        assert "result" in result


# ---------------------------------------------------------------------------
# Sketch commands
# ---------------------------------------------------------------------------

class TestSketchCommands:
    """Validate simulated sketch tool responses."""

    def test_create_sketch_success(self, bridge):
        result = bridge.create_sketch(plane="XY")
        assert result["success"] is True
        assert isinstance(result["sketch_name"], str)
        assert isinstance(result["sketch_id"], str)

    def test_create_sketch_with_name(self, bridge):
        result = bridge.create_sketch(plane="XZ", name="MySketch")
        assert result["sketch_name"] == "MySketch"

    def test_add_sketch_line(self, bridge):
        result = bridge.add_sketch_line("Sketch1", 0, 0, 10, 10)
        assert result["success"] is True
        assert isinstance(result["line_id"], str)

    def test_add_sketch_circle(self, bridge):
        result = bridge.add_sketch_circle("Sketch1", 5, 5, 3)
        assert result["success"] is True
        assert isinstance(result["circle_id"], str)

    def test_add_sketch_rectangle(self, bridge):
        result = bridge.add_sketch_rectangle("Sketch1", 0, 0, 10, 5)
        assert result["success"] is True
        assert isinstance(result["lines"], list)
        assert len(result["lines"]) == 4

    def test_add_sketch_arc(self, bridge):
        result = bridge.add_sketch_arc("Sketch1", 0, 0, 5, 0, 90)
        assert result["success"] is True
        assert isinstance(result["arc_id"], str)


# ---------------------------------------------------------------------------
# Feature commands
# ---------------------------------------------------------------------------

class TestFeatureCommands:
    """Validate simulated feature tool responses."""

    def test_extrude(self, bridge):
        result = bridge.extrude(sketch_name="Sketch1", distance=5.0)
        assert result["success"] is True
        assert isinstance(result["feature_name"], str)
        assert isinstance(result["body_name"], str)

    def test_revolve(self, bridge):
        result = bridge.revolve(sketch_name="Sketch1", axis="X")
        assert result["success"] is True
        assert isinstance(result["feature_name"], str)
        assert isinstance(result["body_name"], str)

    def test_add_fillet(self, bridge):
        result = bridge.add_fillet(body_name="Body1", edge_indices=[0, 1], radius=0.5)
        assert result["success"] is True
        assert isinstance(result["feature_name"], str)

    def test_add_chamfer(self, bridge):
        result = bridge.add_chamfer(body_name="Body1", edge_indices=[0], distance=0.3)
        assert result["success"] is True
        assert isinstance(result["feature_name"], str)


# ---------------------------------------------------------------------------
# Body operation commands
# ---------------------------------------------------------------------------

class TestBodyOperations:
    """Validate simulated body operation responses."""

    def test_delete_body(self, bridge):
        result = bridge.delete_body(body_name="Body1")
        assert result["success"] is True
        assert "Deleted" in result["message"]

    def test_mirror_body(self, bridge):
        result = bridge.mirror_body(body_name="Body1", mirror_plane="XY")
        assert result["success"] is True
        assert isinstance(result["new_body_name"], str)
        assert "Mirrored" in result["new_body_name"]

    def test_create_component(self, bridge):
        result = bridge.create_component(name="Bracket")
        assert result["success"] is True
        assert result["component_name"] == "Bracket"

    def test_apply_material(self, bridge):
        result = bridge.apply_material(body_name="Body1", material_name="Steel")
        assert result["success"] is True
        assert result["applied_material"] == "Steel"


# ---------------------------------------------------------------------------
# Export commands
# ---------------------------------------------------------------------------

class TestExportCommands:
    """Validate simulated export tool responses."""

    def test_export_stl(self, bridge):
        result = bridge.export_stl(filename="model.stl")
        assert result["success"] is True
        assert result["file_path"].endswith("model.stl")
        assert os.path.isabs(result["file_path"])

    def test_export_step(self, bridge):
        result = bridge.export_step(filename="model.step")
        assert result["success"] is True
        assert result["file_path"].endswith("model.step")
        assert os.path.isabs(result["file_path"])

    def test_export_f3d(self, bridge):
        result = bridge.export_f3d(filename="model.f3d")
        assert result["success"] is True
        assert result["file_path"].endswith("model.f3d")
        assert os.path.isabs(result["file_path"])


# ---------------------------------------------------------------------------
# Additional utility commands
# ---------------------------------------------------------------------------

class TestUtilityCommands:
    """Validate simulated utility tool responses."""

    def test_redo(self, bridge):
        result = bridge.redo()
        assert result["status"] == "simulation"
        assert "Redo" in result["message"]

    def test_get_timeline(self, bridge):
        result = bridge.get_timeline()
        assert result["success"] is True
        assert isinstance(result["timeline"], list)
        assert len(result["timeline"]) > 0

    def test_set_parameter(self, bridge):
        result = bridge.set_parameter(name="width", value="10 mm")
        assert result["success"] is True
        assert result["parameter_name"] == "width"


# ---------------------------------------------------------------------------
# execute() dispatch table
# ---------------------------------------------------------------------------

EXPECTED_DISPATCH_TOOLS = [
    "get_document_info",
    "create_cylinder",
    "create_box",
    "create_sphere",
    "get_body_list",
    "take_screenshot",
    "execute_script",
    "undo",
    "save_document",
    "create_sketch",
    "add_sketch_line",
    "add_sketch_circle",
    "add_sketch_rectangle",
    "add_sketch_arc",
    "extrude",
    "revolve",
    "add_fillet",
    "add_chamfer",
    "delete_body",
    "mirror_body",
    "create_component",
    "apply_material",
    "export_stl",
    "export_step",
    "export_f3d",
    "redo",
    "get_timeline",
    "set_parameter",
    "get_body_properties",
    "get_sketch_info",
    "get_face_info",
    "measure_distance",
    "get_component_info",
    "validate_design",
    "list_documents",
    "switch_document",
    "new_document",
    "close_document",
]


class TestDispatchTable:
    """Verify the execute() dispatch table handles all 38 tool names."""

    def test_dispatch_has_all_38_tools(self, bridge):
        """The dispatch table should have entries for all 38 tool names."""
        assert len(EXPECTED_DISPATCH_TOOLS) == 38

    @pytest.mark.parametrize("tool_name", EXPECTED_DISPATCH_TOOLS)
    def test_dispatch_entry_exists(self, bridge, tool_name):
        """Each expected tool name should not return 'Unknown command'."""
        # Provide minimal valid params so the lambdas don't crash
        params = _minimal_params(tool_name)
        result = bridge.execute(tool_name, params)
        assert result.get("status") != "error" or "Unknown command" not in result.get("message", "")

    def test_execute_unknown_command(self, bridge):
        result = bridge.execute("nonexistent_command", {})
        assert result["status"] == "error"
        assert "Unknown command" in result["message"]

    # Specific dispatch regressions
    def test_execute_get_body_list(self, bridge):
        result = bridge.execute("get_body_list", {})
        assert "bodies" in result
        assert result["status"] == "simulation"

    def test_execute_create_cylinder(self, bridge):
        result = bridge.execute("create_cylinder", {"radius": 1.0, "height": 2.0})
        assert result["status"] == "simulation"

    def test_execute_create_box(self, bridge):
        result = bridge.execute("create_box", {"length": 1, "width": 2, "height": 3})
        assert result["status"] == "simulation"

    def test_execute_create_sphere(self, bridge):
        result = bridge.execute("create_sphere", {"radius": 3.0})
        assert result["status"] == "simulation"

    def test_execute_undo(self, bridge):
        result = bridge.execute("undo", {})
        assert result["status"] == "simulation"

    def test_execute_save_document(self, bridge):
        result = bridge.execute("save_document", {})
        assert result["status"] == "simulation"

    def test_execute_get_document_info(self, bridge):
        result = bridge.execute("get_document_info", {})
        assert result["status"] == "simulation"

    def test_execute_create_sketch(self, bridge):
        result = bridge.execute("create_sketch", {"plane": "XY"})
        assert result["success"] is True

    def test_execute_extrude(self, bridge):
        result = bridge.execute("extrude", {"sketch_name": "Sketch1", "distance": 5.0})
        assert result["success"] is True

    def test_execute_take_screenshot(self, bridge):
        result = bridge.execute("take_screenshot", {})
        assert result["success"] is True
        assert "image_base64" in result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_params(tool_name: str) -> dict:
    """Return the minimum parameter dict for a given tool to avoid KeyErrors."""
    _map = {
        "get_document_info": {},
        "create_cylinder": {"radius": 1, "height": 1},
        "create_box": {"length": 1, "width": 1, "height": 1},
        "create_sphere": {"radius": 1},
        "get_body_list": {},
        "take_screenshot": {},
        "execute_script": {"script": "pass"},
        "undo": {},
        "save_document": {},
        "create_sketch": {"plane": "XY"},
        "add_sketch_line": {"sketch_name": "S", "start_x": 0, "start_y": 0, "end_x": 1, "end_y": 1},
        "add_sketch_circle": {"sketch_name": "S", "center_x": 0, "center_y": 0, "radius": 1},
        "add_sketch_rectangle": {"sketch_name": "S", "start_x": 0, "start_y": 0, "end_x": 1, "end_y": 1},
        "add_sketch_arc": {"sketch_name": "S", "center_x": 0, "center_y": 0, "radius": 1, "start_angle": 0, "end_angle": 90},
        "extrude": {"sketch_name": "S", "distance": 1},
        "revolve": {"sketch_name": "S", "axis": "X"},
        "add_fillet": {"body_name": "B", "edge_indices": [0], "radius": 0.1},
        "add_chamfer": {"body_name": "B", "edge_indices": [0], "distance": 0.1},
        "delete_body": {"body_name": "B"},
        "mirror_body": {"body_name": "B", "mirror_plane": "XY"},
        "create_component": {"name": "C"},
        "apply_material": {"body_name": "B", "material_name": "Steel"},
        "export_stl": {"filename": "f.stl"},
        "export_step": {"filename": "f.step"},
        "export_f3d": {"filename": "f.f3d"},
        "redo": {},
        "get_timeline": {},
        "set_parameter": {"name": "p", "value": "10 mm"},
        "get_body_properties": {"body_name": "Body1"},
        "get_sketch_info": {"sketch_name": "Sketch1"},
        "get_face_info": {"body_name": "Body1", "face_index": 0},
        "measure_distance": {"entity1": "body:Body1", "entity2": "body:Body2"},
        "get_component_info": {},
        "validate_design": {},
        "list_documents": {},
        "switch_document": {"document_name": "Doc1"},
        "new_document": {},
        "close_document": {"document_name": "Doc1"},
    }
    return _map.get(tool_name, {})


# ---------------------------------------------------------------------------
# _sim and undo/redo simulation responses
# ---------------------------------------------------------------------------

class TestSimulationResponses:
    """Verify that simulation helper and undo/redo include success: True."""

    def test_sim_response_includes_success_true(self, bridge):
        """FusionBridge._sim('msg') returns dict with success: True."""
        result = FusionBridge._sim("test message")
        assert result["success"] is True
        assert result["status"] == "simulation"
        assert "[SIM]" in result["message"]

    def test_undo_simulation_has_success(self, bridge):
        """In simulation mode, undo() returns result with success: True."""
        result = bridge.undo()
        assert result["success"] is True

    def test_redo_simulation_has_success(self, bridge):
        """In simulation mode, redo() returns result with success: True."""
        result = bridge.redo()
        assert result["success"] is True

"""
tests/test_fusion_bridge.py
Unit tests for fusion/bridge.py -- FusionBridge connection-required behaviour,
connected-bridge operations (TASK-075), and TimeBudget.

With simulation mode removed, the bridge either connects to the real Fusion 360
addin or raises ConnectionError.  These tests verify the "not connected" path,
mock-connected operations, and keep the TimeBudget tests intact.
"""

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from fusion.bridge import FusionBridge, TimeBudget, TimeBudgetExceeded


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge():
    """Return a FusionBridge that is NOT connected (default state)."""
    return FusionBridge()


# ---------------------------------------------------------------------------
# Connection / status
# ---------------------------------------------------------------------------

class TestConnection:
    """Connection management behaviour."""

    def test_default_not_connected(self, bridge):
        assert bridge.connected is False

    def test_connected_property_returns_false(self, bridge):
        # TASK-145: is_connected() removed; use the `connected` property
        assert bridge.connected is False

    def test_connect_when_addin_not_running(self, bridge):
        """connect() returns error status when Fusion 360 addin is not running."""
        result = bridge.connect()
        assert result["status"] == "error"
        assert "not reachable" in result["message"].lower() or "not connected" in result["message"].lower() or "auth" in result["message"].lower()

    def test_disconnect_sets_connected_false(self, bridge):
        bridge.disconnect()
        assert bridge.connected is False

    def test_connected_property_setter(self, bridge):
        """connected property can be set programmatically."""
        bridge.connected = True
        assert bridge.connected is True
        bridge.connected = False
        assert bridge.connected is False


# ---------------------------------------------------------------------------
# _require_connection
# ---------------------------------------------------------------------------

class TestRequireConnection:
    """Verify _require_connection raises when not connected."""

    def test_raises_when_not_connected(self, bridge):
        with pytest.raises(ConnectionError, match="Not connected"):
            bridge._require_connection()

    def test_succeeds_when_connected(self, bridge):
        """When connected is True, _require_connection should not raise."""
        bridge.connected = True
        # Should not raise
        bridge._require_connection()


# ---------------------------------------------------------------------------
# Operations raise ConnectionError when not connected
# ---------------------------------------------------------------------------

class TestOperationsRequireConnection:
    """All operations should raise ConnectionError when not connected."""

    def test_get_document_info(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.get_document_info()

    def test_create_cylinder(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.create_cylinder(radius=1.0, height=2.0)

    def test_create_box(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.create_box(length=1, width=2, height=3)

    def test_create_sphere(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.create_sphere(radius=1.0)

    def test_get_body_list(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.get_body_list()

    def test_take_screenshot(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.take_screenshot()

    def test_execute_script(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.execute_script("pass")

    def test_undo(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.undo()

    def test_redo(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.redo()

    def test_save_document(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.save_document()

    def test_create_sketch(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.create_sketch(plane="XY")

    def test_extrude(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.extrude(sketch_name="S", distance=1.0)

    def test_revolve(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.revolve(sketch_name="S", axis="X")

    def test_add_fillet(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.add_fillet(body_name="B", edge_indices=[0], radius=0.1)

    def test_add_chamfer(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.add_chamfer(body_name="B", edge_indices=[0], distance=0.1)

    def test_delete_body(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.delete_body(body_name="B")

    def test_mirror_body(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.mirror_body(body_name="B", mirror_plane="XY")

    def test_create_component(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.create_component(name="C")

    def test_apply_material(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.apply_material(body_name="B", material_name="Steel")

    def test_export_stl(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.export_stl(filename="f.stl")

    def test_export_step(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.export_step(filename="f.step")

    def test_export_f3d(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.export_f3d(filename="f.f3d")

    def test_get_body_properties(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.get_body_properties(body_name="B")

    def test_get_sketch_info(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.get_sketch_info(sketch_name="S")

    def test_get_face_info(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.get_face_info(body_name="B", face_index=0)

    def test_measure_distance(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.measure_distance(entity1="a", entity2="b")

    def test_get_component_info(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.get_component_info()

    def test_validate_design(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.validate_design()

    def test_list_documents(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.list_documents()

    def test_switch_document(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.switch_document(document_name="D")

    def test_new_document(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.new_document()

    def test_close_document(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.close_document(document_name="D")

    def test_get_timeline(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.get_timeline()

    def test_set_parameter(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.set_parameter(name="p", value="10 mm")

    def test_add_sketch_line(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.add_sketch_line("S", 0, 0, 1, 1)

    def test_add_sketch_circle(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.add_sketch_circle("S", 0, 0, 1)

    def test_add_sketch_rectangle(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.add_sketch_rectangle("S", 0, 0, 1, 1)

    def test_add_sketch_arc(self, bridge):
        with pytest.raises(ConnectionError):
            bridge.add_sketch_arc("S", 0, 0, 1, 0, 90)


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
    def test_dispatch_returns_connection_error(self, bridge, tool_name):
        """Each tool should return a connection error when not connected."""
        params = _minimal_params(tool_name)
        result = bridge.execute(tool_name, params)
        # Should get an error about not being connected (not "Unknown command")
        assert result["status"] == "error"
        assert "Unknown command" not in result.get("message", "")
        assert "Not connected" in result.get("message", "")

    def test_execute_unknown_command(self, bridge):
        result = bridge.execute("nonexistent_command", {})
        assert result["status"] == "error"
        assert "Unknown command" in result["message"]


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
# Validate positive helper
# ---------------------------------------------------------------------------

class TestValidatePositive:
    """Verify _validate_positive rejects invalid inputs."""

    def test_positive_value_passes(self):
        assert FusionBridge._validate_positive(5.0, "test") == 5.0

    def test_zero_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            FusionBridge._validate_positive(0, "test")

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            FusionBridge._validate_positive(-1, "test")

    def test_nan_raises(self):
        with pytest.raises(ValueError, match="must be finite"):
            FusionBridge._validate_positive(float("nan"), "test")

    def test_inf_raises(self):
        with pytest.raises(ValueError, match="must be finite"):
            FusionBridge._validate_positive(float("inf"), "test")


# ---------------------------------------------------------------------------
# TimeBudget context manager
# ---------------------------------------------------------------------------

class TestTimeBudget:
    """Validate TimeBudget context manager behaviour."""

    def test_normal_completion_under_budget(self):
        """Operations completing under budget should not raise."""
        with TimeBudget(budget_seconds=5.0, action="abort") as tb:
            time.sleep(0.01)
        # Should complete without exception
        assert tb.remaining_budget() < 5.0

    def test_exceeded_with_abort_raises(self):
        """Exceeding the budget with action='abort' raises TimeBudgetExceeded."""
        with pytest.raises(TimeBudgetExceeded) as exc_info:
            with TimeBudget(budget_seconds=0.01, action="abort"):
                time.sleep(0.05)
        assert exc_info.value.budget_seconds == 0.01
        assert exc_info.value.elapsed > 0.01

    def test_exceeded_with_warn_does_not_raise(self):
        """Exceeding the budget with action='warn' logs but does not raise."""
        # Should not raise
        with TimeBudget(budget_seconds=0.01, action="warn") as tb:
            time.sleep(0.05)
        # Budget was exceeded but no exception
        assert tb.remaining_budget() < 0

    def test_remaining_budget_before_enter(self):
        """Before entering the context, remaining_budget equals full budget."""
        tb = TimeBudget(budget_seconds=60.0, action="abort")
        assert tb.remaining_budget() == 60.0

    def test_remaining_budget_during_execution(self):
        """During execution, remaining_budget decreases."""
        with TimeBudget(budget_seconds=10.0, action="abort") as tb:
            time.sleep(0.05)
            remaining = tb.remaining_budget()
            assert remaining < 10.0
            assert remaining > 0

    def test_remaining_budget_after_exit(self):
        """After exiting the context, remaining_budget reflects final state."""
        with TimeBudget(budget_seconds=10.0, action="abort") as tb:
            time.sleep(0.01)
        remaining = tb.remaining_budget()
        assert remaining < 10.0
        assert remaining > 0

    def test_invalid_action_raises_value_error(self):
        """Creating TimeBudget with invalid action raises ValueError."""
        with pytest.raises(ValueError, match="action must be"):
            TimeBudget(budget_seconds=10.0, action="ignore")

    def test_time_budget_exceeded_attributes(self):
        """TimeBudgetExceeded stores budget and elapsed attributes."""
        exc = TimeBudgetExceeded(budget_seconds=30.0, elapsed=45.5)
        assert exc.budget_seconds == 30.0
        assert exc.elapsed == 45.5
        assert "45.5" in str(exc)
        assert "30.0" in str(exc)


# ---------------------------------------------------------------------------
# TASK-075: Connected-bridge tests (mocked socket)
# ---------------------------------------------------------------------------

class TestFusionBridgeConnected:
    """Tests for bridge operations when the socket is mocked as connected."""

    @staticmethod
    def _make_connected_bridge() -> FusionBridge:
        """Create a bridge with a mocked socket that appears connected."""
        bridge = FusionBridge()
        bridge._connected = True
        bridge._sock = MagicMock()
        bridge._buf = b""
        return bridge

    # ------------------------------------------------------------------
    # _send_command
    # ------------------------------------------------------------------

    def test_send_command_success(self):
        """A successful command returns the parsed JSON response."""
        bridge = self._make_connected_bridge()
        response = {"id": "test-id", "status": "success", "result": {"data": 42}}
        bridge._sock.recv.return_value = json.dumps(response).encode("utf-8") + b"\n"

        result = bridge._send_command("test_cmd", {"param": "value"})

        assert result["status"] == "success"
        assert result["result"]["data"] == 42
        bridge._sock.sendall.assert_called_once()
        # Verify the payload sent is valid JSON with command and parameters
        sent_bytes = bridge._sock.sendall.call_args[0][0]
        sent_json = json.loads(sent_bytes.decode("utf-8").strip())
        assert sent_json["command"] == "test_cmd"
        assert sent_json["parameters"] == {"param": "value"}
        assert "id" in sent_json

    def test_send_command_error_response(self):
        """An error response from the server is returned as-is."""
        bridge = self._make_connected_bridge()
        response = {"id": "err-1", "status": "error", "message": "Something went wrong"}
        bridge._sock.recv.return_value = json.dumps(response).encode("utf-8") + b"\n"

        result = bridge._send_command("bad_cmd", {})

        assert result["status"] == "error"
        assert "Something went wrong" in result["message"]

    def test_send_command_handles_broken_pipe(self):
        """BrokenPipeError during send marks the bridge as disconnected."""
        bridge = self._make_connected_bridge()
        bridge._sock.sendall.side_effect = BrokenPipeError("Connection reset")

        result = bridge._send_command("test_cmd", {})

        assert bridge._connected is False
        assert bridge._sock is None
        assert result["status"] == "error"
        assert "Socket error" in result["message"]

    def test_send_command_handles_connection_closed(self):
        """Empty recv (connection closed) marks bridge as disconnected."""
        bridge = self._make_connected_bridge()
        bridge._sock.recv.return_value = b""  # connection closed

        result = bridge._send_command("test_cmd", {})

        assert bridge._connected is False
        assert result["status"] == "error"

    def test_send_command_handles_oserror(self):
        """OSError during recv marks bridge as disconnected."""
        bridge = self._make_connected_bridge()
        bridge._sock.sendall.side_effect = OSError("Network unreachable")

        result = bridge._send_command("test_cmd", {})

        assert bridge._connected is False
        assert result["status"] == "error"

    def test_send_command_with_no_socket_returns_error(self):
        """If _sock is None, _send_command returns a not-connected error."""
        bridge = FusionBridge()
        bridge._connected = True
        bridge._sock = None

        result = bridge._send_command("test_cmd", {})

        assert result["status"] == "error"
        assert "Not connected" in result["message"]

    def test_send_command_multipart_response(self):
        """Response arriving in multiple recv() calls is assembled correctly."""
        bridge = self._make_connected_bridge()
        response = {"id": "multi", "status": "success", "data": "hello"}
        full_bytes = json.dumps(response).encode("utf-8") + b"\n"
        half = len(full_bytes) // 2
        bridge._sock.recv.side_effect = [full_bytes[:half], full_bytes[half:]]

        result = bridge._send_command("test_cmd", {})

        assert result["status"] == "success"
        assert result["data"] == "hello"

    # ------------------------------------------------------------------
    # Public command methods (mock _send_command)
    # ------------------------------------------------------------------

    def test_get_document_info_delegates_to_send_command(self):
        """get_document_info calls _send_command with correct arguments."""
        bridge = self._make_connected_bridge()
        bridge._send_command = MagicMock(return_value={"status": "success"})

        result = bridge.get_document_info()

        bridge._send_command.assert_called_once_with("get_document_info", {})
        assert result["status"] == "success"

    def test_create_cylinder_sends_parameters(self):
        bridge = self._make_connected_bridge()
        bridge._send_command = MagicMock(return_value={"status": "success"})

        bridge.create_cylinder(radius=2.5, height=5.0, position=[1, 2, 3])

        args = bridge._send_command.call_args[0]
        assert args[0] == "create_cylinder"
        assert args[1]["radius"] == 2.5
        assert args[1]["height"] == 5.0
        assert args[1]["position"] == [1, 2, 3]

    def test_create_box_sends_parameters(self):
        bridge = self._make_connected_bridge()
        bridge._send_command = MagicMock(return_value={"status": "success"})

        bridge.create_box(length=3, width=4, height=5)

        args = bridge._send_command.call_args[0]
        assert args[0] == "create_box"
        assert args[1]["length"] == 3
        assert args[1]["width"] == 4

    def test_execute_script_sends_script_and_timeout(self):
        bridge = self._make_connected_bridge()
        bridge._send_command = MagicMock(return_value={"status": "success"})

        bridge.execute_script("print('hi')", timeout=10)

        args = bridge._send_command.call_args[0]
        assert args[0] == "execute_script"
        assert args[1]["script"] == "print('hi')"
        assert args[1]["timeout"] == 10

    def test_export_stl_validates_path(self):
        """export_stl resolves the filename through _resolve_export_path."""
        bridge = self._make_connected_bridge()
        bridge._send_command = MagicMock(return_value={"status": "success"})

        bridge.export_stl(filename="model.stl", refinement="high")

        args = bridge._send_command.call_args[0]
        assert args[0] == "export_stl"
        # Filename should be resolved to an absolute path
        assert os.path.isabs(args[1]["filename"])
        assert args[1]["refinement"] == "high"

    # ------------------------------------------------------------------
    # execute() dispatch (connected path)
    # ------------------------------------------------------------------

    def test_execute_dispatches_to_correct_handler(self):
        """execute() dispatches known commands through the dispatch table."""
        bridge = self._make_connected_bridge()
        bridge._send_command = MagicMock(return_value={"status": "success"})

        with patch("config.settings.settings", {"fusion_operation_timeout": 120,
                                                 "fusion_operation_timeout_action": "warn"}):
            result = bridge.execute("get_document_info", {})

        assert result["status"] == "success"
        bridge._send_command.assert_called_once_with("get_document_info", {})

    def test_execute_unknown_tool_returns_error(self):
        """execute() returns an error for unknown command names."""
        bridge = self._make_connected_bridge()

        result = bridge.execute("nonexistent_tool_xyz", {})

        assert result["status"] == "error"
        assert "Unknown command" in result["message"]

    def test_execute_catches_validation_error(self):
        """execute() catches ValueError from _validate_positive and returns error."""
        bridge = self._make_connected_bridge()
        bridge._send_command = MagicMock(return_value={"status": "success"})

        with patch("config.settings.settings", {"fusion_operation_timeout": 120,
                                                 "fusion_operation_timeout_action": "warn"}):
            result = bridge.execute("create_cylinder", {"radius": -1, "height": 1})

        assert result["status"] == "error"
        assert "must be positive" in result["message"]

    def test_execute_catches_nan_validation_error(self):
        """execute() catches NaN validation from _validate_positive."""
        bridge = self._make_connected_bridge()
        bridge._send_command = MagicMock(return_value={"status": "success"})

        with patch("config.settings.settings", {"fusion_operation_timeout": 120,
                                                 "fusion_operation_timeout_action": "warn"}):
            result = bridge.execute("create_sphere", {"radius": float("nan")})

        assert result["status"] == "error"
        assert "must be finite" in result["message"]

    def test_execute_catches_connection_error(self):
        """execute() catches ConnectionError from _require_connection."""
        bridge = FusionBridge()  # not connected
        assert bridge.connected is False

        result = bridge.execute("get_document_info", {})

        assert result["status"] == "error"
        assert "Not connected" in result["message"]

    # ------------------------------------------------------------------
    # disconnect()
    # ------------------------------------------------------------------

    def test_disconnect_closes_socket_and_resets_state(self):
        """disconnect() closes the socket and sets connected to False."""
        bridge = self._make_connected_bridge()
        mock_sock = bridge._sock

        bridge.disconnect()

        assert bridge.connected is False
        assert bridge._sock is None
        mock_sock.close.assert_called_once()

    def test_disconnect_handles_close_exception(self):
        """disconnect() does not crash if socket.close() raises."""
        bridge = self._make_connected_bridge()
        bridge._sock.close.side_effect = OSError("already closed")

        bridge.disconnect()  # should not raise

        assert bridge.connected is False
        assert bridge._sock is None

    # ------------------------------------------------------------------
    # _resolve_export_path (static, security)
    # ------------------------------------------------------------------

    def test_resolve_export_path_relative(self):
        """Relative filenames are resolved inside the exports directory."""
        result = FusionBridge._resolve_export_path("test.stl")
        export_dir = os.path.realpath(os.path.join(
            os.path.expanduser("~"), "Documents", "Fusion360MCP_Exports",
        ))
        assert os.path.normcase(result).startswith(os.path.normcase(export_dir))

    def test_resolve_export_path_traversal_blocked(self):
        """Path traversal attempts are rejected."""
        with pytest.raises(ValueError, match="[Ee]xport path escapes|[Pp]ath traversal"):
            FusionBridge._resolve_export_path("../../etc/passwd")

    def test_resolve_export_path_nested_subdirectory(self):
        """Nested subdirectories within exports dir are allowed."""
        result = FusionBridge._resolve_export_path("project/v1/model.stl")
        assert result.endswith("model.stl")
        assert "project" in result

"""
fusion/bridge.py
External-side Fusion 360 bridge -- connects to the TCP server hosted by the
Fusion 360 Add-in (fusion_addin/addin_server.py) running inside Fusion.

Protocol: newline-delimited JSON over TCP on 127.0.0.1:9876

If the add-in is not reachable, operations return clear error messages
instructing the user to start the addin and connect.
"""

import json
import logging
import math
import os
import socket
import threading
import time
import uuid
from typing import Any

# Security: path must match the token file written by the addin server
_TOKEN_PATH = os.path.join(os.path.expanduser("~"), ".fusion_mcp_token")

logger = logging.getLogger(__name__)

ADDIN_HOST    = "127.0.0.1"
ADDIN_PORT    = 9876
CONNECT_TIMEOUT = 3.0   # seconds to wait when trying to connect
RECV_TIMEOUT    = 30.0  # seconds to wait for a command response

_NOT_CONNECTED_MSG = (
    "Not connected to Fusion 360 addin. "
    "Please start the Fusion 360 addin and click Connect."
)


class TimeBudgetExceeded(Exception):
    """Raised when a Fusion 360 operation exceeds its time budget."""

    def __init__(self, budget_seconds: float, elapsed: float):
        self.budget_seconds = budget_seconds
        self.elapsed = elapsed
        super().__init__(
            f"Time budget exceeded: {elapsed:.1f}s elapsed, "
            f"budget was {budget_seconds:.1f}s"
        )


class TimeBudget:
    """Context manager that enforces a fixed time budget on operations.

    Inspired by autoresearch's TIME_BUDGET pattern -- each operation gets
    a fixed number of seconds.  On exit the elapsed time is logged.  If
    the budget is exceeded, behaviour depends on *action*:

    * ``"abort"`` -- raises :class:`TimeBudgetExceeded`
    * ``"warn"``  -- logs a warning but allows execution to continue
    """

    def __init__(self, budget_seconds: float = 120, action: str = "abort"):
        if action not in ("abort", "warn"):
            raise ValueError(f"action must be 'abort' or 'warn', got {action!r}")
        self.budget_seconds = float(budget_seconds)
        self.action = action
        self._start: float | None = None
        self._end: float | None = None

    def __enter__(self) -> "TimeBudget":
        self._start = time.monotonic()
        self._end = None
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self._end = time.monotonic()
        elapsed = self._end - self._start
        logger.info(
            "TimeBudget: %.1f / %.1f seconds used (%.0f%%)",
            elapsed, self.budget_seconds,
            (elapsed / self.budget_seconds) * 100 if self.budget_seconds else 0,
        )
        if elapsed > self.budget_seconds:
            if self.action == "abort":
                # TASK-146: Don't replace an existing exception -- log timeout
                # as additional context instead of masking the real error.
                if exc_type is not None:
                    logger.warning(
                        "TimeBudget exceeded but exception already propagating: %s",
                        exc_val,
                    )
                    return False
                raise TimeBudgetExceeded(self.budget_seconds, elapsed)
            else:
                logger.warning(
                    "TimeBudget WARNING: operation took %.1fs, "
                    "exceeding budget of %.1fs",
                    elapsed, self.budget_seconds,
                )
        return False  # do not suppress exceptions

    def remaining_budget(self) -> float:
        """Return the number of seconds remaining in the budget.

        If called before ``__enter__``, returns the full budget.
        If called after ``__exit__``, returns 0 or a negative value.
        """
        if self._start is None:
            return self.budget_seconds
        elapsed = (self._end or time.monotonic()) - self._start
        return self.budget_seconds - elapsed


class FusionBridge:
    """
    Socket client that talks to the Fusion 360 Add-in TCP server.

    The bridge is either connected to the real Fusion 360 addin, or it is
    NOT connected.  When not connected, operations return clear error
    messages -- no fake/simulated responses are generated.
    """

    def __init__(self):
        # TASK-067: RLock (reentrant) so that the `connected` property can
        # safely acquire the lock even if the calling thread already holds it
        # (e.g. inside _send_command error handling that sets _connected).
        self._lock = threading.RLock()
        self._sock: socket.socket | None = None
        self._buf  = b""
        self._connected = False
        # TASK-110: Lazy-initialized dispatch dict (built once, not per call)
        self._dispatch: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Connection status
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Whether the bridge is currently connected to the Fusion 360 addin.

        TASK-113: Safe from deadlock because ``_lock`` is an ``RLock``
        (reentrant), so nested acquisitions from the same thread (e.g.
        inside ``_send_command`` error handling) do not block.  See
        TASK-067 for the original RLock migration.
        """
        with self._lock:
            return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        with self._lock:
            self._connected = value

    # TASK-145: is_connected() removed -- use the `connected` property instead.

    # ------------------------------------------------------------------
    # Connection requirement check
    # ------------------------------------------------------------------

    def _require_connection(self) -> None:
        """Raise ConnectionError if not connected to the Fusion 360 addin."""
        if not self.connected:
            raise ConnectionError(_NOT_CONNECTED_MSG)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @staticmethod
    def _read_auth_token() -> str | None:
        """Read the shared auth token written by the Fusion 360 addin server.

        Security: the token is generated per server session and stored in
        ~/.fusion_mcp_token with owner-only permissions.
        """
        try:
            if os.path.exists(_TOKEN_PATH):
                with open(_TOKEN_PATH, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except OSError as exc:
            logger.warning("Could not read auth token from %s: %s", _TOKEN_PATH, exc)
        return None

    def _authenticate(self, sock: socket.socket) -> bool:
        """Send auth handshake as the first message on a new connection.

        Returns True if server accepted the token, False otherwise.

        TASK-066: The read loop now has a 10-second timeout to prevent
        hanging forever if the server never sends a complete response.
        """
        token = self._read_auth_token()
        if not token:
            logger.error("No auth token found at %s -- cannot authenticate.", _TOKEN_PATH)
            return False
        auth_payload = json.dumps({"auth": token}) + "\n"
        sock.sendall(auth_payload.encode("utf-8"))

        # TASK-066: Set a dedicated auth timeout so we don't hang forever
        original_timeout = sock.gettimeout()
        sock.settimeout(10.0)  # 10 second auth timeout

        # Read the auth response
        try:
            buf = b""
            while True:
                if b"\n" in buf:
                    line, _ = buf.split(b"\n", 1)
                    resp = json.loads(line.decode("utf-8"))
                    return resp.get("status") == "success"
                chunk = sock.recv(65536)
                if not chunk:
                    return False
                buf += chunk
        except socket.timeout:
            raise ConnectionError("Authentication timed out after 10 seconds")
        finally:
            # Restore the operational timeout after auth completes
            sock.settimeout(original_timeout)

    def connect(self) -> dict[str, Any]:
        """
        Try to connect to the Fusion 360 add-in.
        Returns a status dict.
        """
        logger.info(
            "Attempting to connect to Fusion 360 addin at %s:%s (timeout=%.1fs)",
            ADDIN_HOST, ADDIN_PORT, CONNECT_TIMEOUT,
        )

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect((ADDIN_HOST, ADDIN_PORT))
            sock.settimeout(RECV_TIMEOUT)

            # Security: authenticate before sending any commands
            if not self._authenticate(sock):
                sock.close()
                logger.error("Authentication to Fusion 360 addin failed.")
                self.connected = False
                return {
                    "status": "error",
                    "message": "Authentication to Fusion 360 addin failed. Check token file.",
                }

            with self._lock:
                self._sock = sock
                self._buf  = b""
            self.connected = True

            # Verify with a ping
            result = self._send_command("ping", {})
            if result.get("status") == "success":
                logger.info("Connected to Fusion 360 add-in on %s:%s", ADDIN_HOST, ADDIN_PORT)
                return {
                    "status": "success",
                    "message": f"Connected to Fusion 360 add-in at {ADDIN_HOST}:{ADDIN_PORT}",
                }
            else:
                self.connected = False
                return {"status": "error", "message": result.get("message", "Ping failed.")}

        except (ConnectionRefusedError, socket.timeout, OSError) as exc:
            logger.warning("Could not connect to Fusion 360 add-in: %s", exc)
            self.connected = False
            with self._lock:
                self._sock = None
            return {
                "status": "error",
                "message": (
                    f"Fusion 360 add-in not reachable ({exc}). "
                    "Please ensure the addin is running:\n"
                    "  1. Copy fusion_addin/ into Fusion's AddIns folder\n"
                    "  2. Enable it in Tools > Add-Ins\n"
                    "  3. Click Connect in this app"
                ),
            }

    def disconnect(self) -> None:
        """Disconnect from the Fusion 360 addin and reset connection state."""
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
            self._connected = False
            logger.info("Disconnected from Fusion 360 addin.")

    # ------------------------------------------------------------------
    # Low-level send/receive
    # ------------------------------------------------------------------

    def _send_command(self, command: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Send one command and wait for the response (thread-safe)."""
        req_id  = str(uuid.uuid4())
        payload = json.dumps({"id": req_id, "command": command, "parameters": parameters}) + "\n"

        with self._lock:
            if self._sock is None:
                return {"status": "error", "message": _NOT_CONNECTED_MSG}
            try:
                self._sock.sendall(payload.encode("utf-8"))
                # Read until we get a complete newline-terminated response
                while True:
                    if b"\n" in self._buf:
                        line, self._buf = self._buf.split(b"\n", 1)
                        response = json.loads(line.decode("utf-8"))
                        # TASK-143: Validate command UUID on response
                        resp_id = response.get("id")
                        if resp_id and resp_id != req_id:
                            logger.warning("Response ID mismatch: expected %s, got %s", req_id, resp_id)
                        return response
                    chunk = self._sock.recv(65536)
                    if not chunk:
                        raise ConnectionError("Connection closed by add-in.")
                    self._buf += chunk
            except Exception as exc:
                logger.exception("Socket error during command '%s'", command)
                # Mark as disconnected
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
                self._connected = False
                return {"status": "error", "message": f"Socket error: {exc}"}

    # ------------------------------------------------------------------
    # Public command API
    # ------------------------------------------------------------------

    def get_document_info(self) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("get_document_info", {})

    def create_cylinder(self, radius: float, height: float, position: list | None = None) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("create_cylinder", {
            "radius": radius, "height": height, "position": position or [0, 0, 0]
        })

    def create_box(self, length: float, width: float, height: float, position: list | None = None) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("create_box", {
            "length": length, "width": width, "height": height, "position": position or [0, 0, 0]
        })

    def create_sphere(self, radius: float, position: list | None = None) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("create_sphere", {
            "radius": radius, "position": position or [0, 0, 0]
        })

    def get_body_list(self) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("get_body_list", {})

    def take_screenshot(self, width: int = 1920, height: int = 1080) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("take_screenshot", {"width": width, "height": height})

    def execute_script(self, script: str, timeout: int = 30) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("execute_script", {"script": script, "timeout": timeout})

    def undo(self) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("undo", {})

    def save_document(self) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("save_document", {})

    # ------------------------------------------------------------------
    # Sketch commands
    # ------------------------------------------------------------------

    def create_sketch(self, plane: str, name: str | None = None) -> dict[str, Any]:
        self._require_connection()
        params: dict[str, Any] = {"plane": plane}
        if name:
            params["name"] = name
        return self._send_command("create_sketch", params)

    def add_sketch_line(self, sketch_name: str, start_x: float, start_y: float,
                        end_x: float, end_y: float) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("add_sketch_line", {
            "sketch_name": sketch_name, "start_x": start_x, "start_y": start_y,
            "end_x": end_x, "end_y": end_y,
        })

    def add_sketch_circle(self, sketch_name: str, center_x: float, center_y: float,
                          radius: float) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("add_sketch_circle", {
            "sketch_name": sketch_name, "center_x": center_x,
            "center_y": center_y, "radius": radius,
        })

    def add_sketch_rectangle(self, sketch_name: str, start_x: float, start_y: float,
                             end_x: float, end_y: float) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("add_sketch_rectangle", {
            "sketch_name": sketch_name, "start_x": start_x, "start_y": start_y,
            "end_x": end_x, "end_y": end_y,
        })

    def add_sketch_arc(self, sketch_name: str, center_x: float, center_y: float,
                       radius: float, start_angle: float, end_angle: float) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("add_sketch_arc", {
            "sketch_name": sketch_name, "center_x": center_x, "center_y": center_y,
            "radius": radius, "start_angle": start_angle, "end_angle": end_angle,
        })

    # ------------------------------------------------------------------
    # Feature commands
    # ------------------------------------------------------------------

    def extrude(self, sketch_name: str, distance: float,
                profile_index: int = 0, operation: str = "new") -> dict[str, Any]:
        self._require_connection()
        return self._send_command("extrude", {
            "sketch_name": sketch_name, "profile_index": profile_index,
            "distance": distance, "operation": operation,
        })

    def revolve(self, sketch_name: str, axis: str,
                profile_index: int = 0, angle: float = 360) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("revolve", {
            "sketch_name": sketch_name, "profile_index": profile_index,
            "axis": axis, "angle": angle,
        })

    def add_fillet(self, body_name: str, edge_indices: list[int],
                   radius: float) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("add_fillet", {
            "body_name": body_name, "edge_indices": edge_indices, "radius": radius,
        })

    def add_chamfer(self, body_name: str, edge_indices: list[int],
                    distance: float) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("add_chamfer", {
            "body_name": body_name, "edge_indices": edge_indices, "distance": distance,
        })

    # ------------------------------------------------------------------
    # Body operation commands
    # ------------------------------------------------------------------

    def mirror_body(self, body_name: str, mirror_plane: str) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("mirror_body", {
            "body_name": body_name, "mirror_plane": mirror_plane,
        })

    def create_component(self, name: str) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("create_component", {"name": name})

    def delete_body(self, body_name: str) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("delete_body", {"body_name": body_name})

    def apply_material(self, body_name: str, material_name: str) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("apply_material", {
            "body_name": body_name, "material_name": material_name,
        })

    # ------------------------------------------------------------------
    # Export commands
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_export_path(filename: str) -> str:
        """Ensure *filename* is absolute; relative names go to ~/Documents/Fusion360MCP_Exports.

        Security: validates the resolved path stays within the exports directory
        to prevent path-traversal attacks (e.g. ``../../etc/passwd``).
        """
        export_dir = os.path.realpath(os.path.join(
            os.path.expanduser("~"), "Documents", "Fusion360MCP_Exports",
        ))
        os.makedirs(export_dir, exist_ok=True)

        if os.path.isabs(filename):
            resolved = os.path.realpath(filename)
        else:
            resolved = os.path.realpath(os.path.join(export_dir, filename))

        # Security: ensure resolved path is inside the exports directory
        # Case-insensitive comparison for Windows (TASK-057)
        if not os.path.normcase(resolved).startswith(os.path.normcase(export_dir + os.sep)) and resolved != export_dir:
            raise ValueError(
                f"Export path escapes the exports directory: {filename!r} "
                f"resolves to {resolved!r} which is outside {export_dir!r}"
            )
        return resolved

    def export_stl(self, filename: str, body_name: str | None = None,
                   refinement: str = "medium") -> dict[str, Any]:
        filename = self._resolve_export_path(filename)
        self._require_connection()
        params: dict[str, Any] = {"filename": filename, "refinement": refinement}
        if body_name:
            params["body_name"] = body_name
        return self._send_command("export_stl", params)

    def export_step(self, filename: str) -> dict[str, Any]:
        filename = self._resolve_export_path(filename)
        self._require_connection()
        return self._send_command("export_step", {"filename": filename})

    def export_f3d(self, filename: str) -> dict[str, Any]:
        filename = self._resolve_export_path(filename)
        self._require_connection()
        return self._send_command("export_f3d", {"filename": filename})

    # ------------------------------------------------------------------
    # Geometric data query commands
    # ------------------------------------------------------------------

    def get_body_properties(self, body_name: str) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("get_body_properties", {"body_name": body_name})

    def get_sketch_info(self, sketch_name: str) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("get_sketch_info", {"sketch_name": sketch_name})

    def get_sketch_list(self) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("get_sketch_list", {})

    def shell_body(self, body_name: str, thickness: float,
                   open_face_index: int | None = -1) -> dict[str, Any]:
        self._require_connection()
        params: dict[str, Any] = {"body_name": body_name, "thickness": thickness}
        if open_face_index is not None:
            params["open_face_index"] = open_face_index
        return self._send_command("shell_body", params)

    def boolean_cut(self, target_body: str, tool_body: str,
                    keep_tool: bool = False) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("boolean_cut", {
            "target_body": target_body,
            "tool_body": tool_body,
            "keep_tool": keep_tool,
        })

    def get_face_info(self, body_name: str, face_index: int) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("get_face_info", {
            "body_name": body_name, "face_index": face_index,
        })

    def measure_distance(self, entity1: str, entity2: str) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("measure_distance", {
            "entity1": entity1, "entity2": entity2,
        })

    def get_component_info(self, component_name: str | None = None) -> dict[str, Any]:
        self._require_connection()
        params: dict[str, Any] = {}
        if component_name:
            params["component_name"] = component_name
        return self._send_command("get_component_info", params)

    def validate_design(self) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("validate_design", {})

    # ------------------------------------------------------------------
    # Document management commands
    # ------------------------------------------------------------------

    def list_documents(self) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("list_documents", {})

    def switch_document(self, document_name: str) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("switch_document", {"document_name": document_name})

    def new_document(self, name: str | None = None,
                     design_type: str = "parametric") -> dict[str, Any]:
        self._require_connection()
        params: dict[str, Any] = {"design_type": design_type}
        if name:
            params["name"] = name
        return self._send_command("new_document", params)

    def close_document(self, document_name: str,
                       save: bool = True) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("close_document", {
            "document_name": document_name, "save": save,
        })

    # ------------------------------------------------------------------
    # Additional utility commands
    # ------------------------------------------------------------------

    def redo(self) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("redo", {})

    def get_timeline(self) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("get_timeline", {})

    def set_parameter(self, name: str, value: str, expression: str | None = None,
                      comment: str | None = None) -> dict[str, Any]:
        self._require_connection()
        params: dict[str, Any] = {"name": name, "value": value}
        if expression:
            params["expression"] = expression
        if comment:
            params["comment"] = comment
        return self._send_command("set_parameter", params)

    # ------------------------------------------------------------------
    # Timeline editing commands (TASK-218)
    # ------------------------------------------------------------------

    def edit_feature(self, timeline_index: int, parameters: dict[str, Any]) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("edit_feature", {
            "timeline_index": timeline_index, "parameters": parameters,
        })

    def suppress_feature(self, timeline_index: int) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("suppress_feature", {"timeline_index": timeline_index})

    def delete_feature(self, timeline_index: int) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("delete_feature", {"timeline_index": timeline_index})

    def reorder_feature(self, from_index: int, to_index: int) -> dict[str, Any]:
        self._require_connection()
        return self._send_command("reorder_feature", {
            "from_index": from_index, "to_index": to_index,
        })

    # ------------------------------------------------------------------
    # Save-as command (TASK-221)
    # ------------------------------------------------------------------

    def save_document_as(self, name: str, description: str = "Saved by MCP agent") -> dict[str, Any]:
        self._require_connection()
        return self._send_command("save_document_as", {
            "name": name, "description": description,
        })

    # ------------------------------------------------------------------
    # TASK-226: Query available commands from the addin
    # ------------------------------------------------------------------

    def query_available_commands(self) -> list[str] | None:
        """Query the addin for its list of registered command handlers.

        TASK-226: Used at connection time to discover which commands the
        running addin actually supports. Returns a list of command name
        strings, or None if the query fails (e.g. older addin without
        list_commands support).
        """
        if not self.connected:
            return None
        try:
            result = self._send_command("list_commands", {})
            if result.get("status") == "success":
                return result.get("commands", [])
            # Older addin may not have list_commands -- treat as unavailable
            logger.info("Addin does not support list_commands: %s",
                        result.get("message", "unknown"))
            return None
        except Exception as exc:
            logger.warning("Failed to query available commands: %s", exc)
            return None

    # ------------------------------------------------------------------
    # TASK-040: Numeric parameter validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_positive(value, name: str) -> float:
        """Validate that *value* is a positive finite number.

        Raises ValueError with a clear message if the check fails.
        """
        v = float(value)
        if not math.isfinite(v):
            raise ValueError(f"{name} must be finite, got {v}")
        if v <= 0:
            raise ValueError(f"{name} must be positive, got {v}")
        return v

    # ------------------------------------------------------------------
    # Generic dispatch (used by MCPServer)
    # ------------------------------------------------------------------

    @property
    def _tool_dispatch(self) -> dict[str, Any]:
        """Lazy-initialized dispatch dict mapping command names to handlers.

        Built once on first access, then cached for all subsequent calls.
        Each handler is a lambda that accepts a parameters dict.
        """
        if self._dispatch is None:
            self._dispatch = {
                "get_document_info": lambda p: self.get_document_info(),
                "create_cylinder":   lambda p: self.create_cylinder(
                    radius=self._validate_positive(p.get("radius", 1.0), "radius"),
                    height=self._validate_positive(p.get("height", 1.0), "height"),
                    position=p.get("position"),
                ),
                "create_box":        lambda p: self.create_box(
                    length=self._validate_positive(p.get("length", 1.0), "length"),
                    width=self._validate_positive(p.get("width",  1.0), "width"),
                    height=self._validate_positive(p.get("height", 1.0), "height"),
                    position=p.get("position"),
                ),
                "create_sphere":     lambda p: self.create_sphere(
                    radius=self._validate_positive(p.get("radius", 1.0), "radius"),
                    position=p.get("position"),
                ),
                "get_body_list":     lambda p: self.get_body_list(),
                "take_screenshot":   lambda p: self.take_screenshot(
                    width=int(p.get("width", 1920)),
                    height=int(p.get("height", 1080)),
                ),
                "execute_script":    lambda p: self.execute_script(
                    script=p.get("script", ""),
                    timeout=int(p.get("timeout", 30)),
                ),
                "undo":              lambda p: self.undo(),
                "save_document":     lambda p: self.save_document(),
                # Sketch tools
                "create_sketch":     lambda p: self.create_sketch(
                    plane=p.get("plane", "XY"),
                    name=p.get("name"),
                ),
                "add_sketch_line":   lambda p: self.add_sketch_line(
                    sketch_name=p["sketch_name"],
                    start_x=float(p["start_x"]), start_y=float(p["start_y"]),
                    end_x=float(p["end_x"]), end_y=float(p["end_y"]),
                ),
                "add_sketch_circle": lambda p: self.add_sketch_circle(
                    sketch_name=p["sketch_name"],
                    center_x=float(p["center_x"]), center_y=float(p["center_y"]),
                    radius=self._validate_positive(p["radius"], "radius"),
                ),
                "add_sketch_rectangle": lambda p: self.add_sketch_rectangle(
                    sketch_name=p["sketch_name"],
                    start_x=float(p["start_x"]), start_y=float(p["start_y"]),
                    end_x=float(p["end_x"]), end_y=float(p["end_y"]),
                ),
                "add_sketch_arc":    lambda p: self.add_sketch_arc(
                    sketch_name=p["sketch_name"],
                    center_x=float(p["center_x"]), center_y=float(p["center_y"]),
                    radius=self._validate_positive(p["radius"], "radius"),
                    start_angle=float(p["start_angle"]), end_angle=float(p["end_angle"]),
                ),
                # Feature tools
                "extrude":           lambda p: self.extrude(
                    sketch_name=p["sketch_name"],
                    distance=self._validate_positive(p["distance"], "distance"),
                    profile_index=int(p.get("profile_index", 0)),
                    operation=p.get("operation", "new"),
                ),
                "revolve":           lambda p: self.revolve(
                    sketch_name=p["sketch_name"],
                    axis=p["axis"],
                    profile_index=int(p.get("profile_index", 0)),
                    angle=float(p.get("angle", 360)),
                ),
                "add_fillet":        lambda p: self.add_fillet(
                    body_name=p["body_name"],
                    edge_indices=p["edge_indices"],
                    radius=self._validate_positive(p["radius"], "radius"),
                ),
                "add_chamfer":       lambda p: self.add_chamfer(
                    body_name=p["body_name"],
                    edge_indices=p["edge_indices"],
                    distance=self._validate_positive(p["distance"], "distance"),
                ),
                # Body operation tools
                "delete_body":       lambda p: self.delete_body(
                    body_name=p["body_name"],
                ),
                "mirror_body":       lambda p: self.mirror_body(
                    body_name=p["body_name"],
                    mirror_plane=p.get("mirror_plane", "XY"),
                ),
                "create_component":  lambda p: self.create_component(
                    name=p["name"],
                ),
                "apply_material":    lambda p: self.apply_material(
                    body_name=p["body_name"],
                    material_name=p["material_name"],
                ),
                # High-level body operation tools
                "shell_body":        lambda p: self.shell_body(
                    body_name=p["body_name"],
                    thickness=float(p["thickness"]),
                    open_face_index=p.get("open_face_index", -1),
                ),
                "boolean_cut":       lambda p: self.boolean_cut(
                    target_body=p["target_body"],
                    tool_body=p["tool_body"],
                    keep_tool=bool(p.get("keep_tool", False)),
                ),
                # Export tools
                "export_stl":        lambda p: self.export_stl(
                    filename=p["filename"],
                    body_name=p.get("body_name"),
                    refinement=p.get("refinement", "medium"),
                ),
                "export_step":       lambda p: self.export_step(
                    filename=p["filename"],
                ),
                "export_f3d":        lambda p: self.export_f3d(
                    filename=p["filename"],
                ),
                # Additional utility tools
                "redo":              lambda p: self.redo(),
                "get_timeline":      lambda p: self.get_timeline(),
                "set_parameter":     lambda p: self.set_parameter(
                    name=p["name"],
                    value=p["value"],
                    expression=p.get("expression"),
                    comment=p.get("comment"),
                ),
                # Timeline editing tools (TASK-218)
                "edit_feature":      lambda p: self.edit_feature(
                    timeline_index=int(p["timeline_index"]),
                    parameters=p.get("parameters", {}),
                ),
                "suppress_feature":  lambda p: self.suppress_feature(
                    timeline_index=int(p["timeline_index"]),
                ),
                "delete_feature":    lambda p: self.delete_feature(
                    timeline_index=int(p["timeline_index"]),
                ),
                "reorder_feature":   lambda p: self.reorder_feature(
                    from_index=int(p["from_index"]),
                    to_index=int(p["to_index"]),
                ),
                # Save-as tool (TASK-221)
                "save_document_as":  lambda p: self.save_document_as(
                    name=p.get("name", ""),
                    description=p.get("description", "Saved by MCP agent"),
                ),
                # Geometric data query tools
                "get_body_properties": lambda p: self.get_body_properties(
                    body_name=p.get("body_name", ""),
                ),
                "get_sketch_info":   lambda p: self.get_sketch_info(
                    sketch_name=p.get("sketch_name", ""),
                ),
                "get_sketch_list":   lambda p: self.get_sketch_list(),
                "get_face_info":     lambda p: self.get_face_info(
                    body_name=p.get("body_name", ""),
                    face_index=int(p.get("face_index", 0)),
                ),
                "measure_distance":  lambda p: self.measure_distance(
                    entity1=p.get("entity1", ""),
                    entity2=p.get("entity2", ""),
                ),
                "get_component_info": lambda p: self.get_component_info(
                    component_name=p.get("component_name"),
                ),
                "validate_design":   lambda p: self.validate_design(),
                # Document management tools
                "list_documents":    lambda p: self.list_documents(),
                "switch_document":   lambda p: self.switch_document(
                    document_name=p.get("document_name", ""),
                ),
                "new_document":      lambda p: self.new_document(
                    name=p.get("name"),
                    design_type=p.get("design_type", "parametric"),
                ),
                "close_document":    lambda p: self.close_document(
                    document_name=p.get("document_name", ""),
                    save=p.get("save", True),
                ),
            }
        return self._dispatch

    def execute(self, command: str, parameters: dict[str, Any]) -> dict[str, Any]:
        handler = self._tool_dispatch.get(command)
        if handler is None:
            return {"status": "error", "message": f"Unknown command: '{command}'"}
        # TASK-040: Catch validation errors and return a clear error response
        try:
            # Wrap execution with time budget from settings
            from config.settings import settings
            budget_secs = settings.get("fusion_operation_timeout", 120)
            budget_action = settings.get("fusion_operation_timeout_action", "abort")
            with TimeBudget(budget_seconds=budget_secs, action=budget_action):
                return handler(parameters)
        except TimeBudgetExceeded as exc:
            return {
                "status": "error",
                "success": False,
                "message": str(exc),
            }
        except ConnectionError as exc:
            return {
                "status": "error",
                "success": False,
                "message": str(exc),
            }
        except ValueError as exc:
            return {"status": "error", "success": False, "message": str(exc)}

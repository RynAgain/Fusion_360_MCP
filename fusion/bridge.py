"""
fusion/bridge.py
External-side Fusion 360 bridge — connects to the TCP server hosted by the
Fusion 360 Add-in (fusion_addin/addin_server.py) running inside Fusion.

Protocol: newline-delimited JSON over TCP on 127.0.0.1:9876

If the add-in is not reachable, falls back to simulation mode automatically.
"""

import json
import logging
import socket
import threading
import uuid
from typing import Any

logger = logging.getLogger(__name__)

ADDIN_HOST    = "127.0.0.1"
ADDIN_PORT    = 9876
CONNECT_TIMEOUT = 3.0   # seconds to wait when trying to connect
RECV_TIMEOUT    = 30.0  # seconds to wait for a command response


class FusionBridge:
    """
    Socket client that talks to the Fusion 360 Add-in TCP server.

    If the add-in is not running (connection refused / timeout), the bridge
    automatically operates in simulation mode and returns descriptive
    simulation responses so the rest of the app keeps working.
    """

    def __init__(self, simulation_mode: bool = False):
        # simulation_mode can be forced True via settings; otherwise we
        # auto-detect by trying to connect.
        self._forced_sim = simulation_mode
        self.simulation_mode = simulation_mode
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._buf  = b""

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> dict[str, Any]:
        """
        Try to connect to the Fusion 360 add-in.
        Returns a status dict and sets self.simulation_mode accordingly.
        """
        if self._forced_sim:
            self.simulation_mode = True
            return {"status": "simulation", "message": "Simulation mode forced in settings."}

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect((ADDIN_HOST, ADDIN_PORT))
            sock.settimeout(RECV_TIMEOUT)
            with self._lock:
                self._sock = sock
                self._buf  = b""
            self.simulation_mode = False

            # Verify with a ping
            result = self._send_command("ping", {})
            if result.get("status") == "success":
                logger.info("Connected to Fusion 360 add-in on %s:%s", ADDIN_HOST, ADDIN_PORT)
                return {"status": "success", "message": f"Connected to Fusion 360 add-in at {ADDIN_HOST}:{ADDIN_PORT}"}
            else:
                return {"status": "error", "message": result.get("message", "Ping failed.")}

        except (ConnectionRefusedError, socket.timeout, OSError) as exc:
            logger.warning("Could not connect to Fusion 360 add-in: %s — using simulation mode.", exc)
            self.simulation_mode = True
            with self._lock:
                self._sock = None
            return {
                "status": "simulation",
                "message": (
                    f"Fusion 360 add-in not reachable ({exc}). "
                    "Running in simulation mode.\n\n"
                    "To connect to real Fusion 360:\n"
                    "  1. Copy fusion_addin/ into Fusion's AddIns folder\n"
                    "  2. Enable it in Tools → Add-Ins\n"
                    "  3. Click Reconnect in this app"
                ),
            }

    def disconnect(self):
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    def is_connected(self) -> bool:
        if self.simulation_mode:
            return True
        with self._lock:
            return self._sock is not None

    # ------------------------------------------------------------------
    # Low-level send/receive
    # ------------------------------------------------------------------

    def _send_command(self, command: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Send one command and wait for the response (thread-safe)."""
        req_id  = str(uuid.uuid4())
        payload = json.dumps({"id": req_id, "command": command, "parameters": parameters}) + "\n"

        with self._lock:
            if self._sock is None:
                return {"status": "error", "message": "Not connected to Fusion 360 add-in."}
            try:
                self._sock.sendall(payload.encode("utf-8"))
                # Read until we get a complete newline-terminated response
                while True:
                    if b"\n" in self._buf:
                        line, self._buf = self._buf.split(b"\n", 1)
                        return json.loads(line.decode("utf-8"))
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
                self.simulation_mode = True
                return {"status": "error", "message": f"Socket error: {exc}"}

    # ------------------------------------------------------------------
    # Simulation responses
    # ------------------------------------------------------------------

    @staticmethod
    def _sim(message: str) -> dict[str, Any]:
        return {"status": "simulation", "message": f"[SIM] {message}"}

    # ------------------------------------------------------------------
    # Public command API
    # ------------------------------------------------------------------

    def get_document_info(self) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status":    "simulation",
                "name":      "SimulatedDocument.f3d",
                "save_path": "/tmp/SimulatedDocument.f3d",
                "is_dirty":  False,
                "message":   "[SIM] No real Fusion 360 connection.",
            }
        return self._send_command("get_document_info", {})

    def create_cylinder(self, radius: float, height: float, position: list | None = None) -> dict[str, Any]:
        if self.simulation_mode:
            return self._sim(f"Created cylinder — radius={radius} cm, height={height} cm, pos={position or [0,0,0]}")
        return self._send_command("create_cylinder", {
            "radius": radius, "height": height, "position": position or [0, 0, 0]
        })

    def create_box(self, length: float, width: float, height: float, position: list | None = None) -> dict[str, Any]:
        if self.simulation_mode:
            return self._sim(f"Created box — {length}×{width}×{height} cm, pos={position or [0,0,0]}")
        return self._send_command("create_box", {
            "length": length, "width": width, "height": height, "position": position or [0, 0, 0]
        })

    def create_sphere(self, radius: float, position: list | None = None) -> dict[str, Any]:
        if self.simulation_mode:
            return self._sim(f"Created sphere — radius={radius} cm, pos={position or [0,0,0]}")
        return self._send_command("create_sphere", {
            "radius": radius, "position": position or [0, 0, 0]
        })

    def get_body_list(self) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "bodies": [
                    {
                        "name": "Body1",
                        "component": "RootComponent",
                        "is_visible": True,
                        "volume": 12.566,
                        "bounding_box": {
                            "min": [-1.0, -1.0, 0.0],
                            "max": [1.0, 1.0, 2.0],
                        },
                    },
                    {
                        "name": "Body2",
                        "component": "RootComponent",
                        "is_visible": True,
                        "volume": 8.0,
                        "bounding_box": {
                            "min": [0.0, 0.0, 0.0],
                            "max": [2.0, 2.0, 2.0],
                        },
                    },
                    {
                        "name": "Body3",
                        "component": "SubComponent1",
                        "is_visible": False,
                        "volume": 4.189,
                        "bounding_box": {
                            "min": [-1.0, -1.0, -1.0],
                            "max": [1.0, 1.0, 1.0],
                        },
                    },
                ],
                "count": 3,
                "message": "[SIM] Returned simulated body list.",
            }
        return self._send_command("get_body_list", {})

    def take_screenshot(self, width: int = 1920, height: int = 1080) -> dict[str, Any]:
        if self.simulation_mode:
            # Return a minimal 1x1 white PNG as base64 placeholder
            placeholder_png = (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
                "2mP8/58BAwAI/AL+hc2rNAAAAABJRU5ErkJggg=="
            )
            return {
                "status": "simulation",
                "success": True,
                "image_base64": placeholder_png,
                "format": "png",
                "width": width,
                "height": height,
                "message": "[SIM] Simulated screenshot (1x1 white pixel placeholder).",
            }
        return self._send_command("take_screenshot", {"width": width, "height": height})

    def execute_script(self, script: str, timeout: int = 30) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "stdout": "[Simulation] Script executed",
                "stderr": "",
                "error": "",
                "result": None,
                "message": "[SIM] Script execution simulated.",
            }
        return self._send_command("execute_script", {"script": script, "timeout": timeout})

    def undo(self) -> dict[str, Any]:
        if self.simulation_mode:
            return self._sim("Undo performed.")
        return self._send_command("undo", {})

    def save_document(self) -> dict[str, Any]:
        if self.simulation_mode:
            return self._sim("Document saved.")
        return self._send_command("save_document", {})

    # ------------------------------------------------------------------
    # Sketch commands
    # ------------------------------------------------------------------

    def create_sketch(self, plane: str, name: str | None = None) -> dict[str, Any]:
        if self.simulation_mode:
            sname = name or "Sketch1"
            return {
                "status": "simulation",
                "success": True,
                "sketch_name": sname,
                "sketch_id": "sim_sketch_1",
                "message": f"[SIM] Created sketch '{sname}' on {plane} plane.",
            }
        params: dict[str, Any] = {"plane": plane}
        if name:
            params["name"] = name
        return self._send_command("create_sketch", params)

    def add_sketch_line(self, sketch_name: str, start_x: float, start_y: float,
                        end_x: float, end_y: float) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "line_id": "sim_line_1",
                "message": f"[SIM] Added line to '{sketch_name}' ({start_x},{start_y})->({end_x},{end_y}).",
            }
        return self._send_command("add_sketch_line", {
            "sketch_name": sketch_name, "start_x": start_x, "start_y": start_y,
            "end_x": end_x, "end_y": end_y,
        })

    def add_sketch_circle(self, sketch_name: str, center_x: float, center_y: float,
                          radius: float) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "circle_id": "sim_circle_1",
                "message": f"[SIM] Added circle to '{sketch_name}' center=({center_x},{center_y}) r={radius}.",
            }
        return self._send_command("add_sketch_circle", {
            "sketch_name": sketch_name, "center_x": center_x,
            "center_y": center_y, "radius": radius,
        })

    def add_sketch_rectangle(self, sketch_name: str, start_x: float, start_y: float,
                             end_x: float, end_y: float) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "lines": ["sim_line_r1", "sim_line_r2", "sim_line_r3", "sim_line_r4"],
                "message": f"[SIM] Added rectangle to '{sketch_name}' ({start_x},{start_y})->({end_x},{end_y}).",
            }
        return self._send_command("add_sketch_rectangle", {
            "sketch_name": sketch_name, "start_x": start_x, "start_y": start_y,
            "end_x": end_x, "end_y": end_y,
        })

    def add_sketch_arc(self, sketch_name: str, center_x: float, center_y: float,
                       radius: float, start_angle: float, end_angle: float) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "arc_id": "sim_arc_1",
                "message": (
                    f"[SIM] Added arc to '{sketch_name}' center=({center_x},{center_y}) "
                    f"r={radius} {start_angle}deg->{end_angle}deg."
                ),
            }
        return self._send_command("add_sketch_arc", {
            "sketch_name": sketch_name, "center_x": center_x, "center_y": center_y,
            "radius": radius, "start_angle": start_angle, "end_angle": end_angle,
        })

    # ------------------------------------------------------------------
    # Feature commands
    # ------------------------------------------------------------------

    def extrude(self, sketch_name: str, distance: float,
                profile_index: int = 0, operation: str = "new") -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "feature_name": "Extrusion1",
                "body_name": "Body1",
                "message": f"[SIM] Extruded '{sketch_name}' profile {profile_index} by {distance} cm ({operation}).",
            }
        return self._send_command("extrude", {
            "sketch_name": sketch_name, "profile_index": profile_index,
            "distance": distance, "operation": operation,
        })

    def revolve(self, sketch_name: str, axis: str,
                profile_index: int = 0, angle: float = 360) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "feature_name": "Revolution1",
                "body_name": "Body1",
                "message": f"[SIM] Revolved '{sketch_name}' profile {profile_index} around {axis} by {angle} deg.",
            }
        return self._send_command("revolve", {
            "sketch_name": sketch_name, "profile_index": profile_index,
            "axis": axis, "angle": angle,
        })

    def add_fillet(self, body_name: str, edge_indices: list[int],
                   radius: float) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "feature_name": "Fillet1",
                "message": f"[SIM] Filleted edges {edge_indices} on '{body_name}' r={radius} cm.",
            }
        return self._send_command("add_fillet", {
            "body_name": body_name, "edge_indices": edge_indices, "radius": radius,
        })

    def add_chamfer(self, body_name: str, edge_indices: list[int],
                    distance: float) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "feature_name": "Chamfer1",
                "message": f"[SIM] Chamfered edges {edge_indices} on '{body_name}' d={distance} cm.",
            }
        return self._send_command("add_chamfer", {
            "body_name": body_name, "edge_indices": edge_indices, "distance": distance,
        })

    # ------------------------------------------------------------------
    # Body operation commands
    # ------------------------------------------------------------------

    def mirror_body(self, body_name: str, mirror_plane: str) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "new_body_name": f"{body_name}_Mirrored",
                "message": f"[SIM] Mirrored '{body_name}' across {mirror_plane}.",
            }
        return self._send_command("mirror_body", {
            "body_name": body_name, "mirror_plane": mirror_plane,
        })

    def create_component(self, name: str) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "component_name": name,
                "message": f"[SIM] Created component '{name}'.",
            }
        return self._send_command("create_component", {"name": name})

    def apply_material(self, body_name: str, material_name: str) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "applied_material": material_name,
                "message": f"[SIM] Applied '{material_name}' to '{body_name}'.",
            }
        return self._send_command("apply_material", {
            "body_name": body_name, "material_name": material_name,
        })

    # ------------------------------------------------------------------
    # Export commands
    # ------------------------------------------------------------------

    def export_stl(self, filename: str, body_name: str | None = None,
                   refinement: str = "medium") -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "file_path": filename,
                "file_size_bytes": 102400,
                "message": f"[SIM] Exported STL to '{filename}' (refinement={refinement}).",
            }
        params: dict[str, Any] = {"filename": filename, "refinement": refinement}
        if body_name:
            params["body_name"] = body_name
        return self._send_command("export_stl", params)

    def export_step(self, filename: str) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "file_path": filename,
                "file_size_bytes": 204800,
                "message": f"[SIM] Exported STEP to '{filename}'.",
            }
        return self._send_command("export_step", {"filename": filename})

    def export_f3d(self, filename: str) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "file_path": filename,
                "file_size_bytes": 512000,
                "message": f"[SIM] Exported F3D to '{filename}'.",
            }
        return self._send_command("export_f3d", {"filename": filename})

    # ------------------------------------------------------------------
    # Geometric data query commands
    # ------------------------------------------------------------------

    def get_body_properties(self, body_name: str) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "name": body_name or "Body1",
                "component": "RootComponent",
                "volume_cm3": 125.0,
                "surface_area_cm2": 150.0,
                "center_of_mass": [2.5, 2.5, 2.5],
                "bounding_box": {"min": [0, 0, 0], "max": [5, 5, 5]},
                "face_count": 6,
                "edge_count": 12,
                "vertex_count": 8,
                "is_solid": True,
                "material": "Steel",
                "appearance": "Steel - Satin",
                "message": f"[SIM] Returned properties for body '{body_name or 'Body1'}'.",
            }
        return self._send_command("get_body_properties", {"body_name": body_name})

    def get_sketch_info(self, sketch_name: str) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "name": sketch_name or "Sketch1",
                "profile_count": 1,
                "is_fully_constrained": True,
                "curves": [
                    {"type": "Line", "start": [0, 0], "end": [5, 0], "length": 5.0},
                    {"type": "Line", "start": [5, 0], "end": [5, 5], "length": 5.0},
                    {"type": "Line", "start": [5, 5], "end": [0, 5], "length": 5.0},
                    {"type": "Line", "start": [0, 5], "end": [0, 0], "length": 5.0},
                ],
                "profiles": [
                    {"index": 0, "area_cm2": 25.0},
                ],
                "dimensions": [
                    {"name": "d1", "value": 5.0, "expression": "5 cm"},
                    {"name": "d2", "value": 5.0, "expression": "5 cm"},
                ],
                "message": f"[SIM] Returned info for sketch '{sketch_name or 'Sketch1'}'.",
            }
        return self._send_command("get_sketch_info", {"sketch_name": sketch_name})

    def get_face_info(self, body_name: str, face_index: int) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "area_cm2": 25.0,
                "surface_type": "PlaneSurfaceType",
                "normal": [0, 0, 1],
                "centroid": [2.5, 2.5, 5.0],
                "edge_count": 4,
                "is_planar": True,
                "message": f"[SIM] Returned face {face_index} info for body '{body_name}'.",
            }
        return self._send_command("get_face_info", {
            "body_name": body_name, "face_index": face_index,
        })

    def measure_distance(self, entity1: str, entity2: str) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "distance_cm": 5.0,
                "point1": [0.0, 0.0, 0.0],
                "point2": [5.0, 0.0, 0.0],
                "message": f"[SIM] Measured distance between '{entity1}' and '{entity2}'.",
            }
        return self._send_command("measure_distance", {
            "entity1": entity1, "entity2": entity2,
        })

    def get_component_info(self, component_name: str | None = None) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "name": component_name or "RootComponent",
                "bodies": ["Body1", "Body2"],
                "sketches": ["Sketch1"],
                "features": [
                    {"name": "Extrude1", "type": "ExtrudeFeature", "is_suppressed": False},
                    {"name": "Fillet1", "type": "FilletFeature", "is_suppressed": False},
                ],
                "children": [
                    {"name": "SubComponent1", "body_count": 1},
                ],
                "occurrence_count": 1,
                "is_root": component_name is None,
                "message": f"[SIM] Returned info for component '{component_name or 'RootComponent'}'.",
            }
        params: dict[str, Any] = {}
        if component_name:
            params["component_name"] = component_name
        return self._send_command("get_component_info", params)

    def validate_design(self) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "valid": True,
                "body_count": 3,
                "component_count": 2,
                "issues": [],
                "message": "[SIM] Design validation passed with no issues.",
            }
        return self._send_command("validate_design", {})

    # ------------------------------------------------------------------
    # Additional utility commands
    # ------------------------------------------------------------------

    def redo(self) -> dict[str, Any]:
        if self.simulation_mode:
            return self._sim("Redo performed.")
        return self._send_command("redo", {})

    def get_timeline(self) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "timeline": [
                    {"index": 0, "name": "Sketch1", "type": "Sketch", "is_suppressed": False, "is_rolled_back": False},
                    {"index": 1, "name": "Extrusion1", "type": "ExtrudeFeature", "is_suppressed": False, "is_rolled_back": False},
                ],
                "message": "[SIM] Returned simulated timeline.",
            }
        return self._send_command("get_timeline", {})

    def set_parameter(self, name: str, value: str, expression: str | None = None) -> dict[str, Any]:
        if self.simulation_mode:
            return {
                "status": "simulation",
                "success": True,
                "parameter_name": name,
                "value": value,
                "expression": expression or value,
                "message": f"[SIM] Set parameter '{name}' = {value}.",
            }
        params: dict[str, Any] = {"name": name, "value": value}
        if expression:
            params["expression"] = expression
        return self._send_command("set_parameter", params)

    # ------------------------------------------------------------------
    # Generic dispatch (used by MCPServer)
    # ------------------------------------------------------------------

    def execute(self, command: str, parameters: dict[str, Any]) -> dict[str, Any]:
        dispatch = {
            "get_document_info": lambda p: self.get_document_info(),
            "create_cylinder":   lambda p: self.create_cylinder(
                radius=float(p.get("radius", 1.0)),
                height=float(p.get("height", 1.0)),
                position=p.get("position"),
            ),
            "create_box":        lambda p: self.create_box(
                length=float(p.get("length", 1.0)),
                width=float(p.get("width",  1.0)),
                height=float(p.get("height", 1.0)),
                position=p.get("position"),
            ),
            "create_sphere":     lambda p: self.create_sphere(
                radius=float(p.get("radius", 1.0)),
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
                radius=float(p["radius"]),
            ),
            "add_sketch_rectangle": lambda p: self.add_sketch_rectangle(
                sketch_name=p["sketch_name"],
                start_x=float(p["start_x"]), start_y=float(p["start_y"]),
                end_x=float(p["end_x"]), end_y=float(p["end_y"]),
            ),
            "add_sketch_arc":    lambda p: self.add_sketch_arc(
                sketch_name=p["sketch_name"],
                center_x=float(p["center_x"]), center_y=float(p["center_y"]),
                radius=float(p["radius"]),
                start_angle=float(p["start_angle"]), end_angle=float(p["end_angle"]),
            ),
            # Feature tools
            "extrude":           lambda p: self.extrude(
                sketch_name=p["sketch_name"],
                distance=float(p["distance"]),
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
                radius=float(p["radius"]),
            ),
            "add_chamfer":       lambda p: self.add_chamfer(
                body_name=p["body_name"],
                edge_indices=p["edge_indices"],
                distance=float(p["distance"]),
            ),
            # Body operation tools
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
            ),
            # Geometric data query tools
            "get_body_properties": lambda p: self.get_body_properties(
                body_name=p.get("body_name", ""),
            ),
            "get_sketch_info":   lambda p: self.get_sketch_info(
                sketch_name=p.get("sketch_name", ""),
            ),
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
        }
        handler = dispatch.get(command)
        if handler is None:
            return {"status": "error", "message": f"Unknown command: '{command}'"}
        return handler(parameters)

"""
fusion_addin/addin_server.py
TCP command server that runs INSIDE Fusion 360's Python interpreter.

Protocol (newline-delimited JSON over TCP):
  Request:  {"id": "uuid", "command": "create_cylinder", "parameters": {...}}\n
  Response: {"id": "uuid", "status": "success"|"error"|"simulation", "message": "...", ...}\n

All Fusion API calls are marshalled back onto the Fusion UI thread via
adsk.core.Application.get().userInterface.commandDefinitions (or a custom event),
because Fusion's API is not thread-safe.
"""

import adsk.core
import adsk.fusion
import adsk.cam

import json
import logging
import os
import secrets
import socket
import stat
import threading
import traceback
import uuid
import queue
import time

# ---------------------------------------------------------------------------
# TASK-081: addin_server Decomposition Plan
#
# _ExecuteEventHandler is ~1600 lines. Extract into:
#   1. geometry_handlers.py (create_box, create_cylinder, create_sphere, etc.)
#   2. sketch_handlers.py (add_sketch_*, sketch operations)
#   3. document_handlers.py (get_document_info, save, close, switch)
#   4. export_handlers.py (export_stl, export_step, etc.)
#   5. script_handlers.py (execute_script with sandbox)
#
# Keep addin_server.py as TCP server + router + auth.
# See FEATURES.md TASK-081 for full details.
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 9876
BUFFER = 65536

# Security: path for shared auth token between addin and client
_TOKEN_PATH = os.path.join(os.path.expanduser("~"), ".fusion_mcp_token")

# Security: modules allowed for import inside execute_script sandbox
_SAFE_IMPORT_ALLOWLIST = frozenset({
    "math", "json", "collections", "itertools", "functools",
    "re", "datetime", "uuid", "string", "decimal", "fractions",
    "statistics", "copy", "enum", "dataclasses", "typing",
})

# Extended allowlist when filesystem access is explicitly granted
_FILESYSTEM_IMPORT_ALLOWLIST = frozenset({
    "os", "os.path", "pathlib", "glob", "shutil", "tempfile",
    "csv", "xml", "html", "base64", "struct", "io",
})

# Security: builtins exposed inside execute_script sandbox
#
# TASK-046: The following builtins are intentionally EXCLUDED to prevent
# sandbox escape via attribute manipulation or introspection:
#   - setattr / delattr / getattr: allow overwriting sandbox restrictions
#     (e.g. ``setattr(__builtins__, 'open', real_open)``).
#   - vars: exposes the namespace dict, enabling sandbox introspection.
#   - type: can create new types with arbitrary bases, enabling code
#     execution outside the sandbox.
#   - object: base class access enables MRO walking to reach restricted
#     builtins (e.g. ``object.__subclasses__()``).
#
# ``hasattr`` is kept because it only performs a read check and cannot
# mutate state.  ``isinstance`` / ``issubclass`` are safe read-only checks.
_SAFE_BUILTINS = {
    # Types & constructors
    "True": True, "False": False, "None": None,
    "bool": bool, "int": int, "float": float, "str": str,
    "bytes": bytes, "bytearray": bytearray,
    "list": list, "tuple": tuple, "dict": dict, "set": set, "frozenset": frozenset,
    "complex": complex, "memoryview": memoryview,
    # Numeric / iteration helpers
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "pow": pow, "divmod": divmod,
    "range": range, "enumerate": enumerate, "zip": zip,
    "map": map, "filter": filter, "sorted": sorted, "reversed": reversed,
    "len": len, "all": all, "any": any, "next": next, "iter": iter,
    # String / repr
    "repr": repr, "str": str, "format": format, "chr": chr, "ord": ord,
    "bin": bin, "hex": hex, "oct": oct, "ascii": ascii,
    # Object helpers (read-only introspection only)
    "isinstance": isinstance, "issubclass": issubclass,
    "id": id, "hash": hash,
    "hasattr": hasattr,
    "callable": callable, "dir": dir,
    # Print (captured stdout)
    "print": print,
    "input": None,  # explicitly blocked
    # Exceptions (scripts need to raise/catch them)
    "Exception": Exception, "TypeError": TypeError, "ValueError": ValueError,
    "KeyError": KeyError, "IndexError": IndexError, "AttributeError": AttributeError,
    "RuntimeError": RuntimeError, "StopIteration": StopIteration,
    "ZeroDivisionError": ZeroDivisionError, "OverflowError": OverflowError,
    "NotImplementedError": NotImplementedError, "ArithmeticError": ArithmeticError,
    # NOTE: exec, eval, compile, __import__, open, globals, locals,
    # getattr, setattr, delattr, vars, type, object are intentionally
    # OMITTED to prevent sandbox escape.
}


class _SafeImporter:
    """Import hook that restricts imports to an explicit allowlist.

    Security: prevents scripts from importing os, sys, subprocess, socket,
    ctypes, shutil, importlib, or any other dangerous module.
    Fusion 360 API modules (adsk.*) are allowed since they are needed for CAD.

    TASK-046: This class is injected as ``__import__`` inside the sandbox's
    ``__builtins__`` dict.  The real ``__import__`` is never exposed to
    sandboxed code -- they can only call *this* callable, which enforces
    the allowlist.  Direct access to ``__import__`` from within the sandbox
    is not possible because it is not placed into ``_SAFE_BUILTINS``.
    """

    def __init__(self, allowlist: frozenset):
        self._allowlist = allowlist

    def __call__(self, name, *args, **kwargs):
        # Allow adsk.* modules -- required for Fusion 360 CAD operations
        if name.startswith("adsk"):
            return __import__(name, *args, **kwargs)
        top_level = name.split(".")[0]
        if top_level not in self._allowlist:
            raise ImportError(
                f"Import of '{name}' is blocked in the script sandbox. "
                f"Allowed modules: {', '.join(sorted(self._allowlist))}"
            )
        return __import__(name, *args, **kwargs)


class FusionCommandServer:
    """
    Starts a background TCP server thread.
    Incoming commands are queued and executed on the Fusion UI thread
    via a custom event handler.
    """

    def __init__(self, app: adsk.core.Application, ui: adsk.core.UserInterface):
        self._app = app
        self._ui  = ui
        self._server_thread: threading.Thread | None = None
        self._running = False
        self._sock: socket.socket | None = None

        # Queue: (command_dict, result_queue) pairs
        self._cmd_queue: queue.Queue = queue.Queue()

        # Register a custom event so we can fire work onto the UI thread
        self._event_id = "FusionMCP_Execute"
        self._custom_event = app.registerCustomEvent(self._event_id)
        self._event_handler = _ExecuteEventHandler(self._cmd_queue, app)
        self._custom_event.add(self._event_handler)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        # Security: generate auth token and write to a known file path
        # so the client (fusion/bridge.py) can read it to authenticate.
        self._auth_token = secrets.token_hex(32)
        try:
            with open(_TOKEN_PATH, "w", encoding="utf-8") as f:
                f.write(self._auth_token)
            # Restrict token file to owner-only read/write (best-effort on Windows)
            try:
                os.chmod(_TOKEN_PATH, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass  # Windows may not fully support POSIX permissions
        except OSError as exc:
            traceback.print_exc()

        self._running = True
        self._server_thread = threading.Thread(target=self._serve, daemon=True)
        self._server_thread.start()

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        # Clean up auth token file
        try:
            if os.path.exists(_TOKEN_PATH):
                os.remove(_TOKEN_PATH)
        except OSError:
            pass
        try:
            self._app.unregisterCustomEvent(self._event_id)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # TCP server loop (background thread)
    # ------------------------------------------------------------------

    def _serve(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # TASK-050: SO_REUSEADDR removed.  On Windows, SO_REUSEADDR
            # allows a second process to bind() to the same port even while
            # the first is still listening, enabling port hijacking.  Without
            # it, a brief TIME_WAIT delay may occur on restart, but that is
            # an acceptable trade-off for preventing address-steal attacks.
            self._sock.bind((HOST, PORT))
            self._sock.listen(5)
            self._sock.settimeout(1.0)

            while self._running:
                try:
                    conn, addr = self._sock.accept()
                    t = threading.Thread(
                        target=self._handle_client,
                        args=(conn, addr),
                        daemon=True,
                    )
                    t.start()
                except socket.timeout:
                    continue
                except Exception:
                    if self._running:
                        traceback.print_exc()
        except Exception:
            traceback.print_exc()

    def _handle_client(self, conn: socket.socket, addr):
        """Handle one client connection — reads newline-delimited JSON commands.

        Security: the first message on every new connection MUST be an auth
        handshake: ``{"auth": "<token>"}``.  If it doesn't match, the
        connection is closed immediately.
        """
        buf = b""
        authenticated = False
        try:
            conn.settimeout(60.0)
            while self._running:
                try:
                    chunk = conn.recv(BUFFER)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue

                        # --- Token authentication gate ---
                        if not authenticated:
                            try:
                                msg = json.loads(line.decode("utf-8"))
                            except json.JSONDecodeError:
                                self._send(conn, {"status": "error", "message": "Invalid auth handshake."})
                                return  # close connection
                            if msg.get("auth") != self._auth_token:
                                self._send(conn, {"status": "error", "message": "Authentication failed."})
                                return  # close connection
                            authenticated = True
                            self._send(conn, {"status": "success", "message": "Authenticated."})
                            continue
                        # --- End auth gate ---

                        try:
                            request = json.loads(line.decode("utf-8"))
                        except json.JSONDecodeError as exc:
                            self._send(conn, {"id": None, "status": "error", "message": f"JSON parse error: {exc}"})
                            continue

                        result = self._dispatch(request)
                        self._send(conn, result)
                except socket.timeout:
                    continue
        except Exception:
            traceback.print_exc()
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _send(self, conn: socket.socket, data: dict):
        try:
            raw = json.dumps(data).encode("utf-8") + b"\n"
            conn.sendall(raw)
        except Exception:
            logger.exception("Failed to send response, closing connection")
            try:
                conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Command dispatch — queues work onto the Fusion UI thread
    # ------------------------------------------------------------------

    def _dispatch(self, request: dict) -> dict:
        req_id  = request.get("id", str(uuid.uuid4()))
        command = request.get("command", "")
        params  = request.get("parameters", {})

        # Put work on the UI-thread queue
        result_q: queue.Queue = queue.Queue()
        self._cmd_queue.put((command, params, result_q))

        # Fire the custom event to wake up the UI thread handler
        self._app.fireCustomEvent(self._event_id, "")

        # Wait for result (up to 30 s)
        try:
            result = result_q.get(timeout=30)
        except queue.Empty:
            result = {"status": "error", "message": "Timeout waiting for Fusion UI thread."}

        result["id"] = req_id
        return result


# ---------------------------------------------------------------------------
# Custom event handler — executes on the Fusion UI thread
# ---------------------------------------------------------------------------

class _ExecuteEventHandler(adsk.core.CustomEventHandler):
    def __init__(self, cmd_queue: queue.Queue, app: adsk.core.Application):
        super().__init__()
        self._queue = cmd_queue
        self._app   = app

    def notify(self, args):
        """Called on the Fusion UI thread each time the custom event fires.

        TASK-063/064: Uses get_nowait() in a while-True loop to eliminate the
        TOCTOU race between empty() and get_nowait().  The except block
        always puts an error dict on result_q so the caller never hangs.
        """
        while True:
            try:
                command, params, result_q = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                result = self._execute(command, params)
                result_q.put(result)
            except Exception:
                logger.exception("Error executing command on UI thread")
                result_q.put({"status": "error", "error": traceback.format_exc()})

    def _execute(self, command: str, params: dict) -> dict:
        """Route command to the appropriate Fusion API call."""
        handlers = {
            "ping":              self._ping,
            "get_document_info": self._get_document_info,
            "create_cylinder":   self._create_cylinder,
            "create_box":        self._create_box,
            "create_sphere":     self._create_sphere,
            "get_body_list":     self._get_body_list,
            "take_screenshot":   self._take_screenshot,
            "execute_script":    self._execute_script,
            "undo":              self._undo,
            "save_document":     self._save_document,
            "save_document_as":  self._handle_save_document_as,
            # Sketch tools
            "create_sketch":        self._handle_create_sketch,
            "add_sketch_line":      self._handle_add_sketch_line,
            "add_sketch_circle":    self._handle_add_sketch_circle,
            "add_sketch_rectangle": self._handle_add_sketch_rectangle,
            "add_sketch_arc":       self._handle_add_sketch_arc,
            # Feature tools
            "extrude":              self._handle_extrude,
            "revolve":              self._handle_revolve,
            "add_fillet":           self._handle_add_fillet,
            "add_chamfer":          self._handle_add_chamfer,
            # Body operation tools
            "delete_body":          self._handle_delete_body,
            "mirror_body":          self._handle_mirror_body,
            "create_component":     self._handle_create_component,
            "apply_material":       self._handle_apply_material,
            # Export tools
            "export_stl":           self._handle_export_stl,
            "export_step":          self._handle_export_step,
            "export_f3d":           self._handle_export_f3d,
            # Geometric data query tools
            "get_body_properties":  self._handle_get_body_properties,
            "get_sketch_info":      self._handle_get_sketch_info,
            "get_face_info":        self._handle_get_face_info,
            "measure_distance":     self._handle_measure_distance,
            "get_component_info":   self._handle_get_component_info,
            "validate_design":      self._handle_validate_design,
            # Additional utility tools
            "redo":                 self._handle_redo,
            "get_timeline":         self._handle_get_timeline,
            "set_parameter":        self._handle_set_parameter,
            # Timeline editing tools (TASK-218)
            "edit_feature":         self._handle_edit_feature,
            "suppress_feature":     self._handle_suppress_feature,
            "delete_feature":       self._handle_delete_feature,
            "reorder_feature":      self._handle_reorder_feature,
            # Document management tools
            "list_documents":       self._handle_list_documents,
            "switch_document":      self._handle_switch_document,
            "new_document":         self._handle_new_document,
            "close_document":       self._handle_close_document,
        }
        fn = handlers.get(command)
        if fn is None:
            return {"status": "error", "message": f"Unknown command: '{command}'"}
        try:
            return fn(params)
        except Exception:
            return {"status": "error", "message": traceback.format_exc()}

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TASK-041: Consistent response helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _success_response(**kwargs) -> dict:
        """Build a consistent success response dict."""
        return {"status": "success", "success": True, **kwargs}

    @staticmethod
    def _error_response(error_msg: str, **kwargs) -> dict:
        """Build a consistent error response dict."""
        return {"status": "error", "success": False, "error": error_msg, **kwargs}

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------

    def _ping(self, p) -> dict:
        return self._success_response(message="pong")

    def _get_document_info(self, p) -> dict:
        doc = self._app.activeDocument
        if not doc:
            return self._error_response("No active document.")
        # Guard against unsaved documents where doc.dataFile is None
        try:
            data_file_name = doc.dataFile.name if doc.dataFile else "Unsaved"
        except Exception:
            data_file_name = "Unsaved"
        try:
            parent_folder = doc.dataFile.parentFolder.name if doc.dataFile and doc.dataFile.parentFolder else "Unknown"
        except Exception:
            parent_folder = "Unknown"

        # TASK-021: Include timeline position so the agent can determine undo depth
        timeline_info = {}
        try:
            design = adsk.fusion.Design.cast(self._app.activeProduct)
            if design:
                timeline = design.timeline
                timeline_info = {
                    "marker_position": timeline.markerPosition,
                    "total_count": timeline.count,
                }
        except Exception:
            pass

        result = {
            "status":        "success",
            "name":          doc.name,
            "data_file":     data_file_name,
            "parent_folder": parent_folder,
            "is_dirty":      doc.isDirty,
        }
        if timeline_info:
            result["timeline_position"] = timeline_info
        return result

    def _root(self):
        design = self._app.activeProduct
        if not isinstance(design, adsk.fusion.Design):
            raise RuntimeError("Active product is not a Fusion 360 Design.")
        return design.rootComponent

    def _create_cylinder(self, p) -> dict:
        name     = p.get("name", "Cylinder")
        radius   = float(p.get("radius", 1.0))
        height   = float(p.get("height", 1.0))
        position = p.get("position", [0, 0, 0])
        px = float(position[0])
        py = float(position[1])
        pz = (float(position[2]) if len(position) > 2 else 0.0)

        root     = self._root()
        sketch   = root.sketches.add(root.xYConstructionPlane)
        center   = adsk.core.Point3D.create(px, py, 0)
        sketch.sketchCurves.sketchCircles.addByCenterRadius(center, radius)

        profile  = sketch.profiles.item(0)
        ext_in   = root.features.extrudeFeatures.createInput(
            profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
        )
        ext_in.setDistanceExtent(False, adsk.core.ValueInput.createByReal(height))
        feature = root.features.extrudeFeatures.add(ext_in)

        body = None
        if feature.bodies.count > 0:
            body = feature.bodies.item(0)
            body.name = name

        # TASK-028: Move the cylinder to the correct Z position if non-zero
        if body and pz != 0.0:
            self._move_body(root, body, 0, 0, pz)

        return {
            "status": "success",
            "success": True,
            "body_name": body.name if body else name,
            "requested_name": name,
            "message": f"Created cylinder r={radius} h={height} at [{px}, {py}, {pz}]",
        }

    def _create_box(self, p) -> dict:
        name     = p.get("name", "Box")
        length   = float(p.get("length", 1.0))
        width    = float(p.get("width",  1.0))
        height   = float(p.get("height", 1.0))
        position = p.get("position", [0, 0, 0])
        px, py   = position[0], position[1]

        root   = self._root()
        sketch = root.sketches.add(root.xYConstructionPlane)
        # TASK-045: Use addTwoPointRectangle for origin-corner semantics.
        # The position is the origin corner; (px + length, py + width) is
        # the opposite corner.
        sketch.sketchCurves.sketchLines.addTwoPointRectangle(
            adsk.core.Point3D.create(px, py, 0),
            adsk.core.Point3D.create(px + length, py + width, 0),
        )
        profile = sketch.profiles.item(0)
        ext_in  = root.features.extrudeFeatures.createInput(
            profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
        )
        ext_in.setDistanceExtent(False, adsk.core.ValueInput.createByReal(height))
        feature = root.features.extrudeFeatures.add(ext_in)

        body = None
        if feature.bodies.count > 0:
            body = feature.bodies.item(0)
            body.name = name

        return self._success_response(
            body_name=body.name if body else name,
            requested_name=name,
            message=f"Created box {length}x{width}x{height}",
        )

    def _create_sphere(self, p) -> dict:
        """Create a sphere using a semicircle profile and revolve."""
        try:
            name = p.get('name', 'Sphere')
            diameter = float(p.get('diameter', 0))
            radius = float(p.get("radius", 1.0))
            position = p.get("position", [0, 0, 0])
            px = (float(position[0]) if len(position) > 0 else 0.0)
            py = (float(position[1]) if len(position) > 1 else 0.0)
            pz = (float(position[2]) if len(position) > 2 else 0.0)

            # TASK-106: Reject conflicting diameter and radius
            if diameter > 0 and radius > 0:
                expected_diameter = radius * 2
                if abs(diameter - expected_diameter) > 0.001:
                    return {
                        "status": "error",
                        "success": False,
                        "error": (
                            f"Conflicting diameter ({diameter}) and radius ({radius}) "
                            f"-- expected diameter={expected_diameter}"
                        ),
                    }

            # Support diameter parameter: if diameter is provided and non-zero, use it
            if diameter > 0:
                radius = diameter / 2.0

            root = self._root()
            sketches = root.sketches

            # Create a new sketch on XZ plane
            xzPlane = root.xZConstructionPlane
            sketch = sketches.add(xzPlane)

            # Draw a semicircle: arc from (0, radius) to (0, -radius) through (radius, 0)
            arcs = sketch.sketchCurves.sketchArcs
            startPoint = adsk.core.Point3D.create(0, radius, 0)
            midPoint = adsk.core.Point3D.create(radius, 0, 0)
            endPoint = adsk.core.Point3D.create(0, -radius, 0)

            arc = arcs.addByThreePoints(startPoint, midPoint, endPoint)

            # Close with a line from end to start (along the Y axis)
            lines = sketch.sketchCurves.sketchLines
            line = lines.addByTwoPoints(arc.endSketchPoint, arc.startSketchPoint)

            # Get the profile
            if sketch.profiles.count == 0:
                return {
                    'status': 'error',
                    'success': False,
                    'error': 'Failed to create sphere profile -- sketch has no closed profiles',
                }

            prof = sketch.profiles.item(0)

            # Create revolve axis (Y axis through origin)
            revolveAxis = root.yConstructionAxis

            # Revolve 360 degrees
            revolves = root.features.revolveFeatures
            revInput = revolves.createInput(
                prof, revolveAxis, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
            )
            angle = adsk.core.ValueInput.createByString('360 deg')
            revInput.setAngleExtent(False, angle)
            revolve = revolves.add(revInput)

            # Name the body
            body = None
            if revolve.bodies.count > 0:
                body = revolve.bodies.item(0)
                body.name = name

            # TASK-027: Move the sphere to the requested position if not at origin
            if body and (px != 0.0 or py != 0.0 or pz != 0.0):
                self._move_body(root, body, px, py, pz)

            return {
                'status': 'success',
                'success': True,
                'body_name': body.name if body else name,
                'requested_name': name,
                'diameter': radius * 2,
                'message': f'Created sphere "{name}" with radius {radius} cm at [{px}, {py}, {pz}]',
            }
        except Exception as e:
            return {'status': 'error', 'success': False, 'error': str(e)}

    def _get_body_list(self, p) -> dict:
        """List all bodies in the design.

        TASK-071: Uses self._app instead of adsk.core.Application.get()
        to be consistent with the rest of the handler methods.
        """
        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            return {'success': False, 'error': 'No active design'}

        bodies = []
        for comp in design.allComponents:
            for i in range(comp.bRepBodies.count):
                body = comp.bRepBodies.item(i)
                bodies.append({
                    'name': body.name,
                    'component': comp.name,
                    'is_visible': body.isVisible,
                    'volume': body.volume,  # cm^3
                    'bounding_box': {
                        'min': [body.boundingBox.minPoint.x, body.boundingBox.minPoint.y, body.boundingBox.minPoint.z],
                        'max': [body.boundingBox.maxPoint.x, body.boundingBox.maxPoint.y, body.boundingBox.maxPoint.z]
                    } if body.boundingBox else None
                })

        return self._success_response(bodies=bodies, count=len(bodies))

    def _take_screenshot(self, p) -> dict:
        """Capture the active viewport as a PNG and return base64-encoded image data."""
        import tempfile
        import base64
        import os

        app = adsk.core.Application.get()
        viewport = app.activeViewport

        # Get optional params
        width = p.get('width', 1920)
        height = p.get('height', 1080)

        # Save to temp file
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, 'fusion_mcp_screenshot.png')

        # saveAsImageFile(filename, width, height)
        success = viewport.saveAsImageFile(temp_path, width, height)

        if success and os.path.exists(temp_path):
            with open(temp_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            os.remove(temp_path)  # cleanup
            return {
                'status': 'success',
                'success': True,
                'image_base64': image_data,
                'format': 'png',
                'width': width,
                'height': height
            }
        else:
            return {'status': 'error', 'success': False, 'error': 'Failed to capture screenshot'}

    def _execute_script(self, p) -> dict:
        """Execute a Python script inside Fusion 360's sandboxed environment.

        Security: the exec namespace is sandboxed -- __builtins__ is replaced
        with a safe subset that excludes exec/eval/compile/__import__/open,
        and a SafeImporter restricts which modules can be imported.
        """
        import io
        import sys
        import traceback as tb

        script_code = p.get('script', '')
        timeout = p.get('timeout', 30)
        allow_filesystem = p.get('allow_filesystem', False)

        # NOTE: timeout parameter is accepted but cannot be enforced for exec() on the UI thread.
        if timeout is not None and timeout != 30:
            logger.warning("Script timeout=%s requested but cannot be enforced on UI thread", timeout)

        if allow_filesystem:
            logger.info("Filesystem access GRANTED for this script execution")

        if not script_code.strip():
            return {'status': 'error', 'success': False, 'error': 'Empty script', 'stdout': '', 'stderr': ''}

        # TASK-065: Reject scripts that exceed a reasonable size limit (100 KB)
        _MAX_SCRIPT_LEN = 102400  # 100 KB
        if len(script_code) > _MAX_SCRIPT_LEN:
            return {
                'status': 'error',
                'success': False,
                'error': f'Script too large ({len(script_code)} bytes). Maximum allowed is {_MAX_SCRIPT_LEN} bytes.',
                'stdout': '',
                'stderr': '',
            }

        # Capture stdout/stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = captured_out = io.StringIO()
        sys.stderr = captured_err = io.StringIO()

        result_value = None
        success = True
        error_msg = ''

        try:
            # Create execution namespace with useful globals
            app = adsk.core.Application.get()
            design = adsk.fusion.Design.cast(app.activeProduct)

            # Security: build a restricted builtins dict -- no exec/eval/
            # compile/__import__/open/globals/locals to prevent sandbox escape.
            safe_builtins = dict(_SAFE_BUILTINS)

            # When allow_filesystem is True, extend the import allowlist
            # to include os, pathlib, etc. and grant open() access.
            # This must be explicitly requested per-script by the agent.
            if allow_filesystem:
                import_allowlist = _SAFE_IMPORT_ALLOWLIST | _FILESYSTEM_IMPORT_ALLOWLIST
                safe_builtins["open"] = open  # Grant file I/O
            else:
                import_allowlist = _SAFE_IMPORT_ALLOWLIST

            safe_builtins["__import__"] = _SafeImporter(import_allowlist)

            exec_globals = {
                '__builtins__': safe_builtins,
                'adsk': adsk,
                'app': app,
                'design': design,
                'rootComp': design.rootComponent if design else None,
                'ui': app.userInterface,
                # Common types as shortcuts (avoid NameError for Point3D, etc.)
                'Point3D': adsk.core.Point3D,
                'Vector3D': adsk.core.Vector3D,
                'Matrix3D': adsk.core.Matrix3D,
                'ObjectCollection': adsk.core.ObjectCollection,
                'ValueInput': adsk.core.ValueInput,
                'FeatureOperations': adsk.fusion.FeatureOperations,
                # Additional common adsk.core types
                'Line3D': getattr(adsk.core, 'Line3D', None),
                'Plane': getattr(adsk.core, 'Plane', None),
                'SurfaceTypes': getattr(adsk.core, 'SurfaceTypes', None),
                # Additional common adsk.fusion types (guarded for version compat)
                'SketchPoint': getattr(adsk.fusion, 'SketchPoint', None),
                'BRepBody': getattr(adsk.fusion, 'BRepBody', None),
                'BRepFace': getattr(adsk.fusion, 'BRepFace', None),
                'BRepEdge': getattr(adsk.fusion, 'BRepEdge', None),
                'TemporaryBRepManager': getattr(adsk.fusion, 'TemporaryBRepManager', None),
                'ExtentDirections': getattr(adsk.fusion, 'ExtentDirections', None),
                'DesignTypes': getattr(adsk.fusion, 'DesignTypes', None),
                'PatternDistanceType': getattr(adsk.fusion, 'PatternDistanceType', None),
                # Standard library modules (pre-imported for convenience)
                'math': __import__('math'),
                'json': __import__('json'),
            }

            exec(script_code, exec_globals)

            # Check if script set a 'result' variable
            result_value = exec_globals.get('result', None)

        except Exception as e:
            success = False
            error_msg = tb.format_exc()
            captured_err.write(error_msg)

        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        stdout_text = captured_out.getvalue()
        stderr_text = captured_err.getvalue()

        # TASK-013: Detect partial failures -- the script may catch
        # exceptions internally (printing errors) while exec() itself
        # does not raise.  If the captured output contains error
        # indicators, flag the result so the agent sees the problem.
        _ERROR_INDICATORS = ("Error", "Failed", "Exception", "Traceback", "error:", "failed:")
        partial_failure = False
        warnings_list: list[str] = []

        if success:
            for indicator in _ERROR_INDICATORS:
                if indicator in stdout_text or indicator in stderr_text:
                    partial_failure = True
                    break

            if partial_failure:
                # Collect lines that look like errors
                for line in (stdout_text + "\n" + stderr_text).splitlines():
                    for indicator in _ERROR_INDICATORS:
                        if indicator in line:
                            warnings_list.append(line.strip())
                            break

        response = {
            'status': 'error' if (not success or partial_failure) else 'success',
            'success': success and not partial_failure,
            'stdout': stdout_text,
            'stderr': stderr_text,
            'error': error_msg,
            'result': str(result_value) if result_value is not None else None,
        }

        if partial_failure:
            response['partial_failure'] = True
            response['warnings'] = warnings_list[:20]  # cap at 20 lines

        return response

    def _undo(self, p) -> dict:
        """Undo operation(s) using the design timeline.

        TASK-021: Accepts an optional ``count`` parameter to undo multiple
        steps.  Returns the new timeline position after undo.
        """
        try:
            app = adsk.core.Application.get()
            design = adsk.fusion.Design.cast(app.activeProduct)

            if not design:
                return {'status': 'error', 'success': False, 'error': 'No active design'}

            # TASK-021: Support multi-step undo via optional count parameter
            count = int(p.get('count', 1)) if isinstance(p, dict) else 1
            count = max(1, count)  # at least 1

            timeline = design.timeline
            start_position = timeline.markerPosition

            if start_position <= 0:
                return {
                    'status': 'error',
                    'success': False,
                    'error': 'Nothing to undo -- already at the beginning of the timeline',
                    'timeline_position': start_position,
                    'timeline_total': timeline.count,
                }

            undos_performed = 0
            for _ in range(count):
                old_pos = timeline.markerPosition
                if old_pos <= 0:
                    break
                timeline.markerPosition = old_pos - 1
                # Verify the position actually changed
                if timeline.markerPosition >= old_pos:
                    break  # timeline didn't move, stop
                undos_performed += 1

            return {
                'status': 'success',
                'success': True,
                'message': f'Undo successful ({undos_performed} step(s)). Timeline position: {timeline.markerPosition}/{timeline.count}',
                'undos_performed': undos_performed,
                'undos_requested': count,
                'timeline_position': timeline.markerPosition,
                'timeline_total': timeline.count,
            }
        except Exception as e:
            return {'status': 'error', 'success': False, 'error': str(e)}

    def _save_document(self, p) -> dict:
        try:
            app = adsk.core.Application.get()
            doc = app.activeDocument

            if not doc:
                return {'success': False, 'error': 'No active document'}

            # TASK-221: Never-saved documents -- guide user to save_document_as
            if doc.dataFile is None:
                return {
                    'success': False,
                    'status': 'error',
                    'error': (
                        'Document has never been saved. Use save_document_as '
                        'with a name parameter to save it for the first time.'
                    ),
                }

            # Document has been saved before -- just save
            doc.save('Auto-save from MCP agent')
            return {'success': True, 'status': 'success', 'message': f'Document "{doc.name}" saved'}
        except Exception as e:
            return {'success': False, 'status': 'error', 'error': str(e)}

    # ------------------------------------------------------------------
    # Helper: find sketch by name
    # ------------------------------------------------------------------

    def _find_sketch(self, sketch_name: str):
        """Find a sketch by name in all components. Returns the sketch or raises RuntimeError."""
        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            raise RuntimeError("No active design.")
        for comp in design.allComponents:
            for i in range(comp.sketches.count):
                sk = comp.sketches.item(i)
                if sk.name == sketch_name:
                    return sk
        raise RuntimeError(f"Sketch '{sketch_name}' not found.")

    def _find_body(self, body_name: str):
        """Find a body by name in all components.

        TASK-020/TASK-105: Exact match first, then prefix match fallback.
        If only partial matches exist, returns the first one (for backward
        compatibility with callers that expect a single body).
        """
        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            raise RuntimeError("No active design.")

        # TASK-105: Exact match first (full scan)
        for comp in design.allComponents:
            for i in range(comp.bRepBodies.count):
                body = comp.bRepBodies.item(i)
                if body.name == body_name:
                    return body

        # TASK-105: Prefix match fallback
        for comp in design.allComponents:
            for i in range(comp.bRepBodies.count):
                body = comp.bRepBodies.item(i)
                if body.name.startswith(body_name) or body_name.startswith(body.name):
                    return body

        raise RuntimeError(f"Body '{body_name}' not found.")

    def _find_all_matching_bodies(self, body_name: str) -> list:
        """Find all bodies whose name matches or contains *body_name*.

        TASK-020: Used by get_body_properties to return all matches when
        the query is ambiguous, so the agent can disambiguate.
        """
        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            raise RuntimeError("No active design.")

        exact = []
        partial = []

        for comp in design.allComponents:
            for i in range(comp.bRepBodies.count):
                body = comp.bRepBodies.item(i)
                if body.name == body_name:
                    exact.append(body)
                elif body_name in body.name:
                    partial.append(body)

        return exact if exact else partial

    def _move_body(self, root, body, dx: float, dy: float, dz: float):
        """Translate a body by (dx, dy, dz) using MoveFeatures.

        TASK-027/028: Shared helper for positioning bodies after creation.
        """
        move_feats = root.features.moveFeatures
        bodies_collection = adsk.core.ObjectCollection.create()
        bodies_collection.add(body)

        transform = adsk.core.Matrix3D.create()
        transform.translation = adsk.core.Vector3D.create(dx, dy, dz)

        move_input = move_feats.createInput2(bodies_collection)
        move_input.defineAsFreeMove(transform)
        move_feats.add(move_input)

    def _handle_delete_body(self, params) -> dict:
        """Delete a body from the design by name."""
        body_name = params.get('body_name', '')
        try:
            body = self._find_body(body_name)
        except RuntimeError:
            return {'success': False, 'error': f'Body "{body_name}" not found'}
        try:
            body.deleteMe()
            return {'success': True, 'status': 'success', 'message': f'Deleted body "{body_name}"'}
        except Exception as e:
            return {'success': False, 'status': 'error', 'error': str(e)}

    def _get_construction_plane(self, plane_str: str):
        """Return the construction plane matching 'XY', 'XZ', or 'YZ'."""
        root = self._root()
        planes = {
            "XY": root.xYConstructionPlane,
            "XZ": root.xZConstructionPlane,
            "YZ": root.yZConstructionPlane,
        }
        p = planes.get(plane_str.upper())
        if p is None:
            raise RuntimeError(f"Unknown plane '{plane_str}'. Use XY, XZ, or YZ.")
        return p

    # ------------------------------------------------------------------
    # Sketch tool handlers
    # ------------------------------------------------------------------

    def _handle_create_sketch(self, p) -> dict:
        plane_str = p.get("plane", "XY")
        name = p.get("name")

        root = self._root()
        plane = self._get_construction_plane(plane_str)
        sketch = root.sketches.add(plane)
        if name:
            sketch.name = name

        return {
            "status": "success",
            "success": True,
            "sketch_name": sketch.name,
            "sketch_id": sketch.entityToken if hasattr(sketch, "entityToken") else str(id(sketch)),
        }

    def _handle_add_sketch_line(self, p) -> dict:
        sketch = self._find_sketch(p["sketch_name"])
        start = adsk.core.Point3D.create(float(p["start_x"]), float(p["start_y"]), 0)
        end   = adsk.core.Point3D.create(float(p["end_x"]),   float(p["end_y"]),   0)
        line  = sketch.sketchCurves.sketchLines.addByTwoPoints(start, end)
        return {
            "status": "success",
            "success": True,
            "line_id": line.entityToken if hasattr(line, "entityToken") else str(id(line)),
        }

    def _handle_add_sketch_circle(self, p) -> dict:
        sketch = self._find_sketch(p["sketch_name"])
        center = adsk.core.Point3D.create(float(p["center_x"]), float(p["center_y"]), 0)
        circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(center, float(p["radius"]))
        return {
            "status": "success",
            "success": True,
            "circle_id": circle.entityToken if hasattr(circle, "entityToken") else str(id(circle)),
        }

    def _handle_add_sketch_rectangle(self, p) -> dict:
        sketch = self._find_sketch(p["sketch_name"])
        pt1 = adsk.core.Point3D.create(float(p["start_x"]), float(p["start_y"]), 0)
        pt2 = adsk.core.Point3D.create(float(p["end_x"]),   float(p["end_y"]),   0)
        lines = sketch.sketchCurves.sketchLines.addTwoPointRectangle(pt1, pt2)
        line_ids = []
        for i in range(lines.count):
            ln = lines.item(i)
            line_ids.append(ln.entityToken if hasattr(ln, "entityToken") else str(id(ln)))
        return {
            "status": "success",
            "success": True,
            "lines": line_ids,
        }

    def _handle_add_sketch_arc(self, p) -> dict:
        import math

        sketch = self._find_sketch(p["sketch_name"])
        cx = float(p["center_x"])
        cy = float(p["center_y"])
        radius = float(p["radius"])
        start_deg = float(p["start_angle"])
        end_deg   = float(p["end_angle"])

        start_rad = math.radians(start_deg)
        sweep_rad = math.radians(end_deg - start_deg)

        center = adsk.core.Point3D.create(cx, cy, 0)
        start_pt = adsk.core.Point3D.create(
            cx + radius * math.cos(start_rad),
            cy + radius * math.sin(start_rad),
            0,
        )
        arc = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(center, start_pt, sweep_rad)
        return {
            "status": "success",
            "success": True,
            "arc_id": arc.entityToken if hasattr(arc, "entityToken") else str(id(arc)),
        }

    # ------------------------------------------------------------------
    # Feature tool handlers
    # ------------------------------------------------------------------

    def _handle_extrude(self, p) -> dict:
        sketch = self._find_sketch(p["sketch_name"])
        profile_idx = int(p.get("profile_index", 0))
        distance = float(p["distance"])
        op_str = p.get("operation", "new").lower()

        if profile_idx >= sketch.profiles.count:
            return {"status": "error", "message": f"Profile index {profile_idx} out of range (sketch has {sketch.profiles.count} profiles)."}
        profile = sketch.profiles.item(profile_idx)

        op_map = {
            "new":       adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
            "join":      adsk.fusion.FeatureOperations.JoinFeatureOperation,
            "cut":       adsk.fusion.FeatureOperations.CutFeatureOperation,
            "intersect": adsk.fusion.FeatureOperations.IntersectFeatureOperation,
        }
        op = op_map.get(op_str, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

        root = self._root()
        ext_input = root.features.extrudeFeatures.createInput(profile, op)
        ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(distance))

        # TASK-019: Auto-populate participantBodies for cut/intersect/join ops
        # to prevent "No target body found to cut or intersect!" errors.
        participant_warning = None
        if op_str in ("cut", "intersect", "join"):
            try:
                if ext_input.participantBodies.count == 0:
                    solid_count = 0
                    for i in range(root.bRepBodies.count):
                        body = root.bRepBodies.item(i)
                        if body.isSolid:
                            ext_input.participantBodies.add(body)
                            solid_count += 1
                    if solid_count > 0:
                        participant_warning = (
                            f"[AUTO] participantBodies was empty; auto-populated "
                            f"with {solid_count} solid body/bodies from active component."
                        )
            except Exception:
                pass  # participantBodies may not be available for all ops

        feature = root.features.extrudeFeatures.add(ext_input)

        body_name = ""
        if feature.bodies.count > 0:
            body_name = feature.bodies.item(0).name

        result = {
            "status": "success",
            "success": True,
            "feature_name": feature.name,
            "body_name": body_name,
        }
        if participant_warning:
            result["participant_warning"] = participant_warning
        return result

    def _handle_revolve(self, p) -> dict:
        import math

        sketch = self._find_sketch(p["sketch_name"])
        profile_idx = int(p.get("profile_index", 0))
        axis_str = p.get("axis", "Z")
        angle_deg = float(p.get("angle", 360))

        if profile_idx >= sketch.profiles.count:
            return {"status": "error", "message": f"Profile index {profile_idx} out of range."}
        profile = sketch.profiles.item(profile_idx)

        root = self._root()
        # Determine axis
        axis_map = {
            "X": root.xConstructionAxis,
            "Y": root.yConstructionAxis,
            "Z": root.zConstructionAxis,
        }
        axis = axis_map.get(axis_str.upper())
        if axis is None:
            # Try to interpret as a sketch line reference (by index)
            try:
                line_idx = int(axis_str)
                axis = sketch.sketchCurves.sketchLines.item(line_idx)
            except (ValueError, RuntimeError):
                return {"status": "error", "message": f"Unknown axis '{axis_str}'. Use X, Y, Z, or a sketch line index."}

        rev_input = root.features.revolveFeatures.createInput(
            profile, axis, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
        )
        rev_input.setAngleExtent(False, adsk.core.ValueInput.createByReal(math.radians(angle_deg)))
        feature = root.features.revolveFeatures.add(rev_input)

        body_name = ""
        if feature.bodies.count > 0:
            body_name = feature.bodies.item(0).name

        return {
            "status": "success",
            "success": True,
            "feature_name": feature.name,
            "body_name": body_name,
        }

    def _handle_add_fillet(self, p) -> dict:
        body = self._find_body(p["body_name"])
        edge_indices = p["edge_indices"]
        radius = float(p["radius"])

        root = self._root()
        fillet_input = root.features.filletFeatures.createInput()
        edges = adsk.core.ObjectCollection.create()
        for idx in edge_indices:
            if idx >= body.edges.count:
                return {"status": "error", "message": f"Edge index {idx} out of range (body has {body.edges.count} edges)."}
            edges.add(body.edges.item(idx))
        fillet_input.addConstantRadiusEdgeSet(edges, adsk.core.ValueInput.createByReal(radius), True)
        feature = root.features.filletFeatures.add(fillet_input)

        return {
            "status": "success",
            "success": True,
            "feature_name": feature.name,
        }

    def _handle_add_chamfer(self, p) -> dict:
        body = self._find_body(p["body_name"])
        edge_indices = p["edge_indices"]
        distance = float(p["distance"])

        root = self._root()
        chamfer_input = root.features.chamferFeatures.createInput2()
        edges = adsk.core.ObjectCollection.create()
        for idx in edge_indices:
            if idx >= body.edges.count:
                return {"status": "error", "message": f"Edge index {idx} out of range (body has {body.edges.count} edges)."}
            edges.add(body.edges.item(idx))
        chamfer_input.chamferEdgeSets.addEqualDistanceChamferEdgeSet(
            edges, adsk.core.ValueInput.createByReal(distance), True
        )
        feature = root.features.chamferFeatures.add(chamfer_input)

        return {
            "status": "success",
            "success": True,
            "feature_name": feature.name,
        }

    # ------------------------------------------------------------------
    # Body operation tool handlers
    # ------------------------------------------------------------------

    def _handle_mirror_body(self, p) -> dict:
        body = self._find_body(p["body_name"])
        plane_str = p.get("mirror_plane", "XY")
        plane = self._get_construction_plane(plane_str)

        root = self._root()
        input_entities = adsk.core.ObjectCollection.create()
        input_entities.add(body)

        mirror_input = root.features.mirrorFeatures.createInput(input_entities, plane)
        feature = root.features.mirrorFeatures.add(mirror_input)

        new_body_name = ""
        if feature.bodies.count > 0:
            new_body_name = feature.bodies.item(0).name

        return {
            "status": "success",
            "success": True,
            "new_body_name": new_body_name,
        }

    def _handle_create_component(self, p) -> dict:
        name = p.get("name", "NewComponent")
        root = self._root()
        occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        comp = occ.component
        comp.name = name
        return {
            "status": "success",
            "success": True,
            "component_name": comp.name,
        }

    def _handle_apply_material(self, p) -> dict:
        body = self._find_body(p["body_name"])
        material_name = p.get("material_name", "")

        app = self._app
        # Search material libraries for the requested material
        found_material = None
        available = []
        for lib in app.materialLibraries:
            for i in range(lib.materials.count):
                mat = lib.materials.item(i)
                available.append(mat.name)
                if mat.name.lower() == material_name.lower():
                    found_material = mat
                    break
            if found_material:
                break

        if not found_material:
            # Try partial match
            for lib in app.materialLibraries:
                for i in range(lib.materials.count):
                    mat = lib.materials.item(i)
                    if material_name.lower() in mat.name.lower():
                        found_material = mat
                        break
                if found_material:
                    break

        if not found_material:
            return {
                "status": "error",
                "message": f"Material '{material_name}' not found. Available materials (first 20): {available[:20]}",
            }

        body.material = found_material
        return {
            "status": "success",
            "success": True,
            "applied_material": found_material.name,
        }

    # ------------------------------------------------------------------
    # Export tool handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_export_path(filename: str) -> str:
        """Ensure *filename* is an absolute path.

        Relative names are placed under ~/Documents/Fusion360MCP_Exports
        so that exports work identically on Windows and macOS.

        TASK-057: Validates the resolved path stays within the exports
        directory to prevent path-traversal attacks.
        """
        import os

        export_dir = os.path.realpath(os.path.join(
            os.path.expanduser("~"), "Documents", "Fusion360MCP_Exports",
        ))
        os.makedirs(export_dir, exist_ok=True)

        if os.path.isabs(filename):
            resolved = os.path.realpath(filename)
        else:
            resolved = os.path.realpath(os.path.join(export_dir, filename))

        # Security: case-insensitive comparison for Windows (TASK-057)
        if not os.path.normcase(resolved).startswith(os.path.normcase(export_dir + os.sep)) and resolved != export_dir:
            raise ValueError(f"Path traversal blocked: {filename}")

        return resolved

    def _handle_export_stl(self, p) -> dict:
        import os

        filename = self._resolve_export_path(p.get("filename", "export.stl"))
        body_name = p.get("body_name")
        refinement = p.get("refinement", "medium").lower()

        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            return {"status": "error", "message": "No active design."}

        export_mgr = design.exportManager

        # TASK-112: Create STL export options only once.  Previously, options
        # were created for rootComponent then overwritten when body_name was
        # provided, wasting the first allocation.
        if body_name:
            body = self._find_body(body_name)
            stl_opts = export_mgr.createSTLExportOptions(body)
        else:
            stl_opts = export_mgr.createSTLExportOptions(design.rootComponent)

        # Set refinement
        refinement_map = {
            "low":    adsk.fusion.MeshRefinementSettings.MeshRefinementLow,
            "medium": adsk.fusion.MeshRefinementSettings.MeshRefinementMedium,
            "high":   adsk.fusion.MeshRefinementSettings.MeshRefinementHigh,
        }
        stl_opts.meshRefinement = refinement_map.get(
            refinement, adsk.fusion.MeshRefinementSettings.MeshRefinementMedium
        )
        stl_opts.filename = filename
        export_mgr.execute(stl_opts)

        file_size = os.path.getsize(filename) if os.path.exists(filename) else 0
        return {
            "status": "success",
            "success": True,
            "file_path": filename,
            "file_size_bytes": file_size,
        }

    def _handle_export_step(self, p) -> dict:
        import os

        filename = self._resolve_export_path(p.get("filename", "export.step"))

        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            return {"status": "error", "message": "No active design."}

        export_mgr = design.exportManager
        step_opts = export_mgr.createSTEPExportOptions(filename, design.rootComponent)
        export_mgr.execute(step_opts)

        file_size = os.path.getsize(filename) if os.path.exists(filename) else 0
        return {
            "status": "success",
            "success": True,
            "file_path": filename,
            "file_size_bytes": file_size,
        }

    def _handle_export_f3d(self, p) -> dict:
        import os

        filename = self._resolve_export_path(p.get("filename", "export.f3d"))

        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            return {"status": "error", "message": "No active design."}

        export_mgr = design.exportManager
        f3d_opts = export_mgr.createFusionArchiveExportOptions(filename)
        export_mgr.execute(f3d_opts)

        file_size = os.path.getsize(filename) if os.path.exists(filename) else 0
        return {
            "status": "success",
            "success": True,
            "file_path": filename,
            "file_size_bytes": file_size,
        }

    # ------------------------------------------------------------------
    # Geometric data query tool handlers
    # ------------------------------------------------------------------

    def _handle_get_body_properties(self, params) -> dict:
        body_name = params.get('body_name', '')

        # TASK-020: Check for multiple matching bodies to help disambiguate
        try:
            all_matches = self._find_all_matching_bodies(body_name)
        except RuntimeError:
            all_matches = []

        if not all_matches:
            return {'success': False, 'error': f'Body "{body_name}" not found'}

        # If there are multiple matches, include them all for disambiguation
        if len(all_matches) > 1:
            # Check if there's an exact name match among them
            exact = [b for b in all_matches if b.name == body_name]
            if len(exact) == 1:
                # Single exact match -- use it, but still report the others
                body = exact[0]
            else:
                # No single exact match -- return all matches for disambiguation
                multiple_matches = []
                for b in all_matches:
                    match_info = {
                        'name': b.name,
                        'volume': b.physicalProperties.volume if b.isSolid else 0,
                        'component': b.parentComponent.name,
                    }
                    try:
                        bb = b.boundingBox
                        match_info['bounding_box'] = {
                            'min': [bb.minPoint.x, bb.minPoint.y, bb.minPoint.z],
                            'max': [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z],
                        }
                    except Exception:
                        pass
                    multiple_matches.append(match_info)
                return {
                    'success': True,
                    'ambiguous': True,
                    'message': f'Multiple bodies match "{body_name}". Specify the exact name.',
                    'multiple_matches': multiple_matches,
                }
        else:
            body = all_matches[0]

        phys = body.physicalProperties
        bb = body.boundingBox

        result = {
            'success': True,
            'name': body.name,
            'component': body.parentComponent.name,
            'volume_cm3': phys.volume,
            'surface_area_cm2': phys.area,
            'center_of_mass': [phys.centerOfMass.x, phys.centerOfMass.y, phys.centerOfMass.z],
            'bounding_box': {
                'min': [bb.minPoint.x, bb.minPoint.y, bb.minPoint.z],
                'max': [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z]
            },
            'face_count': body.faces.count,
            'edge_count': body.edges.count,
            'vertex_count': body.vertices.count,
            'is_solid': body.isSolid,
            'material': body.material.name if body.material else None,
            'appearance': body.appearance.name if body.appearance else None
        }

        # TASK-020: If there were other partial matches, include them as context
        if len(all_matches) > 1:
            other_names = [b.name for b in all_matches if b.name != body.name]
            if other_names:
                result['similar_bodies'] = other_names

        return result

    def _handle_get_sketch_info(self, params) -> dict:
        sketch_name = params.get('sketch_name', '')
        try:
            sketch = self._find_sketch(sketch_name)
        except RuntimeError:
            return {'success': False, 'error': f'Sketch "{sketch_name}" not found'}

        curves = []
        for i in range(sketch.sketchCurves.count):
            curve = sketch.sketchCurves.item(i)
            curve_info = {'type': type(curve).__name__.replace('SketchFitted', '').replace('Sketch', '')}
            if hasattr(curve, 'startSketchPoint') and hasattr(curve, 'endSketchPoint'):
                curve_info['start'] = [curve.startSketchPoint.geometry.x, curve.startSketchPoint.geometry.y]
                curve_info['end'] = [curve.endSketchPoint.geometry.x, curve.endSketchPoint.geometry.y]
            if hasattr(curve, 'length'):
                curve_info['length'] = curve.length
            if hasattr(curve, 'centerSketchPoint'):
                curve_info['center'] = [curve.centerSketchPoint.geometry.x, curve.centerSketchPoint.geometry.y]
            if hasattr(curve, 'radius'):
                curve_info['radius'] = curve.radius
            curves.append(curve_info)

        profiles = []
        for i in range(sketch.profiles.count):
            prof = sketch.profiles.item(i)
            profiles.append({
                'index': i,
                'area_cm2': prof.areaProperties().area
            })

        dimensions = []
        for i in range(sketch.sketchDimensions.count):
            dim = sketch.sketchDimensions.item(i)
            dimensions.append({
                'name': dim.parameter.name if dim.parameter else f'dim_{i}',
                'value': dim.parameter.value if dim.parameter else 0,
                'expression': dim.parameter.expression if dim.parameter else ''
            })

        return {
            'success': True,
            'name': sketch.name,
            'profile_count': sketch.profiles.count,
            'is_fully_constrained': sketch.isFullyConstrained if hasattr(sketch, 'isFullyConstrained') else None,
            'curves': curves,
            'profiles': profiles,
            'dimensions': dimensions
        }

    def _handle_get_face_info(self, params) -> dict:
        body_name = params.get('body_name', '')
        face_index = int(params.get('face_index', 0))

        try:
            body = self._find_body(body_name)
        except RuntimeError:
            return {'success': False, 'error': f'Body "{body_name}" not found'}

        if face_index >= body.faces.count:
            return {'success': False, 'error': f'Face index {face_index} out of range (body has {body.faces.count} faces).'}

        face = body.faces.item(face_index)
        evaluator = face.evaluator

        # Get area
        area = face.area

        # Get surface type
        surface_type = face.geometry.surfaceType if hasattr(face.geometry, 'surfaceType') else 'Unknown'

        # Get normal at parametric center (for planar faces)
        is_planar = hasattr(face.geometry, 'normal')
        normal = None
        if is_planar:
            n = face.geometry.normal
            normal = [n.x, n.y, n.z]
        else:
            # Try evaluating normal at the centroid
            ret_val, param = evaluator.getParameterAtPoint(face.pointOnFace)
            if ret_val:
                ret_val2, n = evaluator.getNormalAtParameter(param)
                if ret_val2:
                    normal = [n.x, n.y, n.z]

        # Get centroid via point on face
        centroid = None
        pt = face.pointOnFace
        if pt:
            centroid = [pt.x, pt.y, pt.z]

        return {
            'success': True,
            'area_cm2': area,
            'surface_type': str(surface_type),
            'normal': normal,
            'centroid': centroid,
            'edge_count': face.edges.count,
            'is_planar': is_planar
        }

    def _resolve_entity(self, ref_str):
        """
        Parse an entity reference string and return the corresponding Fusion entity.
        Formats: 'body:Name', 'face:BodyName:index', 'edge:BodyName:index'
        """
        parts = ref_str.split(':')
        if len(parts) < 2:
            raise RuntimeError(f"Invalid entity reference: '{ref_str}'. Use 'body:Name', 'face:BodyName:index', or 'edge:BodyName:index'.")

        entity_type = parts[0].lower()

        if entity_type == 'body':
            body_name = ':'.join(parts[1:])  # handle colons in body names
            return self._find_body(body_name)

        elif entity_type == 'face':
            if len(parts) < 3:
                raise RuntimeError(f"Face reference requires format 'face:BodyName:index', got '{ref_str}'.")
            body_name = parts[1]
            face_idx = int(parts[2])
            body = self._find_body(body_name)
            if face_idx >= body.faces.count:
                raise RuntimeError(f"Face index {face_idx} out of range (body '{body_name}' has {body.faces.count} faces).")
            return body.faces.item(face_idx)

        elif entity_type == 'edge':
            if len(parts) < 3:
                raise RuntimeError(f"Edge reference requires format 'edge:BodyName:index', got '{ref_str}'.")
            body_name = parts[1]
            edge_idx = int(parts[2])
            body = self._find_body(body_name)
            if edge_idx >= body.edges.count:
                raise RuntimeError(f"Edge index {edge_idx} out of range (body '{body_name}' has {body.edges.count} edges).")
            return body.edges.item(edge_idx)

        else:
            raise RuntimeError(f"Unknown entity type '{entity_type}' in reference '{ref_str}'. Use 'body', 'face', or 'edge'.")

    def _handle_measure_distance(self, params) -> dict:
        entity1_str = params.get('entity1', '')
        entity2_str = params.get('entity2', '')

        try:
            entity1 = self._resolve_entity(entity1_str)
            entity2 = self._resolve_entity(entity2_str)
        except RuntimeError as e:
            return {'success': False, 'error': str(e)}

        app = adsk.core.Application.get()
        measure_mgr = app.measureManager
        result = measure_mgr.measureMinimumDistance(entity1, entity2)

        if not result:
            return {'success': False, 'error': 'Measure failed — could not compute distance between the two entities.'}

        return {
            'success': True,
            'distance_cm': result.value,
            'point1': [result.pointOne.x, result.pointOne.y, result.pointOne.z],
            'point2': [result.pointTwo.x, result.pointTwo.y, result.pointTwo.z]
        }

    def _handle_get_component_info(self, params) -> dict:
        component_name = params.get('component_name')

        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            return {'success': False, 'error': 'No active design.'}

        comp = None
        if not component_name:
            comp = design.rootComponent
        else:
            for c in design.allComponents:
                if c.name == component_name:
                    comp = c
                    break
            if not comp:
                return {'success': False, 'error': f'Component "{component_name}" not found.'}

        bodies = []
        for i in range(comp.bRepBodies.count):
            bodies.append(comp.bRepBodies.item(i).name)

        sketches = []
        for i in range(comp.sketches.count):
            sketches.append(comp.sketches.item(i).name)

        features_list = []
        if hasattr(comp, 'features'):
            timeline = design.timeline
            for i in range(timeline.count):
                item = timeline.item(i)
                entity = item.entity
                if entity and hasattr(entity, 'parentComponent') and entity.parentComponent == comp:
                    features_list.append({
                        'name': entity.name if hasattr(entity, 'name') else f'Feature_{i}',
                        'type': entity.objectType if hasattr(entity, 'objectType') else 'Unknown',
                        'is_suppressed': item.isSuppressed if hasattr(item, 'isSuppressed') else False,
                    })

        children = []
        for i in range(comp.occurrences.count):
            occ = comp.occurrences.item(i)
            child_comp = occ.component
            children.append({
                'name': child_comp.name,
                'body_count': child_comp.bRepBodies.count,
            })

        return {
            'success': True,
            'name': comp.name,
            'bodies': bodies,
            'sketches': sketches,
            'features': features_list,
            'children': children,
            'occurrence_count': comp.occurrences.count,
            'is_root': comp == design.rootComponent,
        }

    def _handle_validate_design(self, params) -> dict:
        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            return {'success': False, 'error': 'No active design.'}

        issues = []
        body_count = 0
        component_count = 0

        for comp in design.allComponents:
            component_count += 1
            for i in range(comp.bRepBodies.count):
                body = comp.bRepBodies.item(i)
                body_count += 1

                # Check if body is solid
                if not body.isSolid:
                    issues.append({
                        'type': 'NON_SOLID',
                        'severity': 'warning',
                        'description': f"Body '{body.name}' is not solid",
                        'entity': body.name,
                    })

                # Check for very small bodies (volume near zero)
                try:
                    if body.isSolid and body.physicalProperties.volume < 1e-6:
                        issues.append({
                            'type': 'TINY_BODY',
                            'severity': 'warning',
                            'description': f"Body '{body.name}' has extremely small volume ({body.physicalProperties.volume} cm^3)",
                            'entity': body.name,
                        })
                except Exception:
                    pass

        valid = len([i for i in issues if i['severity'] == 'error']) == 0

        return {
            'success': True,
            'valid': valid,
            'body_count': body_count,
            'component_count': component_count,
            'issues': issues,
        }

    # ------------------------------------------------------------------
    # Additional utility tool handlers
    # ------------------------------------------------------------------

    def _handle_redo(self, p) -> dict:
        """Redo last undone operation using the design timeline."""
        try:
            app = adsk.core.Application.get()
            design = adsk.fusion.Design.cast(app.activeProduct)

            if not design:
                return {'status': 'error', 'success': False, 'error': 'No active design'}

            timeline = design.timeline
            if timeline.markerPosition < timeline.count:
                timeline.markerPosition = timeline.markerPosition + 1
                return {
                    'status': 'success',
                    'success': True,
                    'message': f'Redo successful. Timeline position: {timeline.markerPosition}',
                }
            else:
                return {
                    'status': 'error',
                    'success': False,
                    'error': 'Nothing to redo',
                }
        except Exception as e:
            return {'status': 'error', 'success': False, 'error': str(e)}

    def _handle_get_timeline(self, p) -> dict:
        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            return {"status": "error", "message": "No active design."}

        timeline = design.timeline
        items = []
        for i in range(timeline.count):
            item = timeline.item(i)
            entity = item.entity
            item_info = {
                "index": i,
                "name": entity.name if entity and hasattr(entity, "name") else f"TimelineItem_{i}",
                "type": entity.objectType if entity and hasattr(entity, "objectType") else "Unknown",
                "is_suppressed": item.isSuppressed if hasattr(item, "isSuppressed") else False,
                "is_rolled_back": item.isRolledBack if hasattr(item, "isRolledBack") else False,
            }
            items.append(item_info)

        return {
            "status": "success",
            "success": True,
            "timeline": items,
        }

    # ------------------------------------------------------------------
    # Timeline editing tool handlers (TASK-218)
    # ------------------------------------------------------------------

    def _handle_edit_feature(self, p) -> dict:
        """Edit an existing feature's parameters by timeline index.

        TASK-218: Enables surgical edits to specific timeline features
        instead of requiring a full project rebuild.
        """
        try:
            index = int(p.get("timeline_index", -1))
            new_params = p.get("parameters", {})

            if index < 0:
                return self._error_response("timeline_index is required and must be >= 0.")

            if not new_params:
                return self._error_response("parameters dict is required and must not be empty.")

            design = adsk.fusion.Design.cast(self._app.activeProduct)
            if not design:
                return self._error_response("No active design.")

            timeline = design.timeline
            if index >= timeline.count:
                return self._error_response(
                    f"Timeline index {index} out of range (timeline has {timeline.count} items)."
                )

            item = timeline.item(index)
            entity = item.entity

            if entity is None:
                return self._error_response(
                    f"Timeline item at index {index} has no editable entity."
                )

            # Apply parameter changes to the feature entity
            modified = []
            for param_name, param_value in new_params.items():
                if hasattr(entity, param_name):
                    try:
                        setattr(entity, param_name, param_value)
                        modified.append(param_name)
                    except Exception as e:
                        return self._error_response(
                            f"Failed to set '{param_name}' on feature: {e}"
                        )
                else:
                    return self._error_response(
                        f"Feature at index {index} has no attribute '{param_name}'. "
                        f"Entity type: {entity.objectType if hasattr(entity, 'objectType') else 'Unknown'}"
                    )

            return self._success_response(
                message=f"Edited feature at timeline index {index}. Modified: {modified}",
                timeline_index=index,
                modified_params=modified,
            )
        except Exception as e:
            return self._error_response(str(e))

    def _handle_suppress_feature(self, p) -> dict:
        """Suppress (disable) a feature at a given timeline index.

        TASK-218: Suppressed features remain in the timeline but have no
        effect on geometry, allowing the agent to disable failed operations
        without deleting them.
        """
        try:
            index = int(p.get("timeline_index", -1))

            if index < 0:
                return self._error_response("timeline_index is required and must be >= 0.")

            design = adsk.fusion.Design.cast(self._app.activeProduct)
            if not design:
                return self._error_response("No active design.")

            timeline = design.timeline
            if index >= timeline.count:
                return self._error_response(
                    f"Timeline index {index} out of range (timeline has {timeline.count} items)."
                )

            item = timeline.item(index)

            if item.isSuppressed:
                return self._success_response(
                    message=f"Feature at index {index} is already suppressed.",
                    timeline_index=index,
                    already_suppressed=True,
                )

            item.isSuppressed = True

            return self._success_response(
                message=f"Suppressed feature at timeline index {index}.",
                timeline_index=index,
            )
        except Exception as e:
            return self._error_response(str(e))

    def _handle_delete_feature(self, p) -> dict:
        """Delete a feature at a given timeline index.

        TASK-218: Permanently removes a feature from the timeline.
        Use suppress_feature if you may want to re-enable it later.
        """
        try:
            index = int(p.get("timeline_index", -1))

            if index < 0:
                return self._error_response("timeline_index is required and must be >= 0.")

            design = adsk.fusion.Design.cast(self._app.activeProduct)
            if not design:
                return self._error_response("No active design.")

            timeline = design.timeline
            if index >= timeline.count:
                return self._error_response(
                    f"Timeline index {index} out of range (timeline has {timeline.count} items)."
                )

            item = timeline.item(index)
            entity = item.entity

            if entity is None:
                return self._error_response(
                    f"Timeline item at index {index} has no deletable entity."
                )

            feature_name = entity.name if hasattr(entity, "name") else f"TimelineItem_{index}"
            entity.deleteMe()

            return self._success_response(
                message=f"Deleted feature '{feature_name}' at timeline index {index}.",
                timeline_index=index,
                deleted_feature=feature_name,
            )
        except Exception as e:
            return self._error_response(str(e))

    def _handle_reorder_feature(self, p) -> dict:
        """Move a timeline feature from one index to another.

        TASK-218: Allows reordering features in the timeline to fix
        sequencing issues without recreating features.
        """
        try:
            from_index = int(p.get("from_index", -1))
            to_index = int(p.get("to_index", -1))

            if from_index < 0:
                return self._error_response("from_index is required and must be >= 0.")
            if to_index < 0:
                return self._error_response("to_index is required and must be >= 0.")

            design = adsk.fusion.Design.cast(self._app.activeProduct)
            if not design:
                return self._error_response("No active design.")

            timeline = design.timeline
            if from_index >= timeline.count:
                return self._error_response(
                    f"from_index {from_index} out of range (timeline has {timeline.count} items)."
                )
            if to_index >= timeline.count:
                return self._error_response(
                    f"to_index {to_index} out of range (timeline has {timeline.count} items)."
                )

            item = timeline.item(from_index)
            item.moveToIndex(to_index)

            return self._success_response(
                message=f"Moved feature from index {from_index} to index {to_index}.",
                from_index=from_index,
                to_index=to_index,
            )
        except Exception as e:
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # Save-as handler (TASK-221)
    # ------------------------------------------------------------------

    def _handle_save_document_as(self, p) -> dict:
        """Save the active document with a new name.

        TASK-221: Provides programmatic save-as for new documents that
        have never been saved, avoiding the 'Use File > Save As' error.
        """
        try:
            name = p.get("name", "")
            description = p.get("description", "Saved by MCP agent")
            if not name:
                return self._error_response("name parameter is required.")

            app = adsk.core.Application.get()
            doc = app.activeDocument
            if not doc:
                return self._error_response("No active document.")

            # Get the root folder of the active project
            data_folder = app.data.activeProject.rootFolder

            doc.saveAs(name, data_folder, description, "")

            return self._success_response(
                message=f'Document saved as "{name}".',
                document_name=name,
            )
        except Exception as e:
            return self._error_response(str(e))

    def _handle_set_parameter(self, p) -> dict:
        name = p.get("name", "")
        value = p.get("value", "")
        expression = p.get("expression")

        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            return {"status": "error", "message": "No active design."}

        # Search in all parameters
        param = None
        for i in range(design.allParameters.count):
            candidate = design.allParameters.item(i)
            if candidate.name == name:
                param = candidate
                break

        if param is None:
            # Try user parameters
            for i in range(design.userParameters.count):
                candidate = design.userParameters.item(i)
                if candidate.name == name:
                    param = candidate
                    break

        if param is None:
            return {"status": "error", "message": f"Parameter '{name}' not found."}

        if expression:
            param.expression = expression
        else:
            param.expression = value

        return {
            "status": "success",
            "success": True,
            "parameter_name": param.name,
            "value": param.expression,
            "expression": param.expression,
        }

    # ------------------------------------------------------------------
    # Document management tool handlers
    # ------------------------------------------------------------------

    def _handle_list_documents(self, params) -> dict:
        """List all open documents in Fusion 360."""
        try:
            app = adsk.core.Application.get()
            docs = []
            active_doc = app.activeDocument

            for i in range(app.documents.count):
                doc = app.documents.item(i)
                docs.append({
                    'name': doc.name,
                    'id': doc.dataFile.id if doc.dataFile else f'unsaved_{i}',
                    'is_active': doc == active_doc,
                    'is_saved': doc.isSaved,
                    'version': doc.dataFile.versionNumber if doc.dataFile else 0,
                    'data_file': doc.dataFile.name if doc.dataFile else 'Untitled',
                })

            return {
                'success': True,
                'documents': docs,
                'count': len(docs),
                'active_document': active_doc.name if active_doc else None,
            }
        except Exception:
            return {'success': False, 'error': traceback.format_exc()}

    def _handle_switch_document(self, params) -> dict:
        """Switch to a different open document by name."""
        try:
            doc_name = params.get('document_name', '')
            app = adsk.core.Application.get()

            for i in range(app.documents.count):
                doc = app.documents.item(i)
                if doc.name == doc_name:
                    doc.activate()
                    return {
                        'success': True,
                        'active_document': doc.name,
                        'message': f'Switched to "{doc.name}"',
                    }

            return {'success': False, 'error': f'Document "{doc_name}" not found'}
        except Exception:
            return {'success': False, 'error': traceback.format_exc()}

    def _handle_new_document(self, params) -> dict:
        """Create a new Fusion 360 design document."""
        try:
            app = adsk.core.Application.get()
            doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)

            design_type = params.get('design_type', 'parametric')
            design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
            if design and design_type == 'direct':
                design.designType = adsk.fusion.DesignTypes.DirectDesignType

            # Note: document name is set on save, not on creation in F360
            return {
                'success': True,
                'document_name': doc.name,
                'message': f'Created new {design_type} design document',
            }
        except Exception:
            return {'success': False, 'error': traceback.format_exc()}

    def _handle_close_document(self, params) -> dict:
        """Close an open document by name, optionally saving first.

        TASK-111: Removed the explicit ``doc.save()`` call before
        ``doc.close(save_first)`` -- ``close(True)`` already saves the
        document, so the prior code caused a double-save.
        """
        try:
            doc_name = params.get('document_name', '')
            save_first = params.get('save', True)
            app = adsk.core.Application.get()

            for i in range(app.documents.count):
                doc = app.documents.item(i)
                if doc.name == doc_name:
                    # TASK-111: Let close(save_first=True) handle saving;
                    # no separate doc.save() call needed.
                    doc.close(save_first)
                    return {
                        'success': True,
                        'message': f'Closed "{doc_name}"' + (' (saved)' if save_first else ' (not saved)'),
                    }

            return {'success': False, 'error': f'Document "{doc_name}" not found'}
        except Exception:
            return {'success': False, 'error': traceback.format_exc()}

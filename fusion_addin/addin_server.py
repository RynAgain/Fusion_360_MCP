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
import socket
import threading
import traceback
import uuid
import queue
import time

HOST = "127.0.0.1"
PORT = 9876
BUFFER = 65536


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
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
        """Handle one client connection — reads newline-delimited JSON commands."""
        buf = b""
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
            conn.sendall((json.dumps(data) + "\n").encode("utf-8"))
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
        """Called on the Fusion UI thread each time the custom event fires."""
        while not self._queue.empty():
            try:
                command, params, result_q = self._queue.get_nowait()
                result = self._execute(command, params)
                result_q.put(result)
            except Exception:
                try:
                    result_q.put({"status": "error", "message": traceback.format_exc()})
                except Exception:
                    pass

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

    def _ping(self, p) -> dict:
        return {"status": "success", "message": "pong"}

    def _get_document_info(self, p) -> dict:
        doc = self._app.activeDocument
        if not doc:
            return {"status": "error", "message": "No active document."}
        return {
            "status":    "success",
            "name":      doc.name,
            "save_path": doc.savePath,
            "is_dirty":  doc.isDirty,
        }

    def _root(self):
        design = self._app.activeProduct
        if not isinstance(design, adsk.fusion.Design):
            raise RuntimeError("Active product is not a Fusion 360 Design.")
        return design.rootComponent

    def _create_cylinder(self, p) -> dict:
        radius   = float(p.get("radius", 1.0))
        height   = float(p.get("height", 1.0))
        position = p.get("position", [0, 0, 0])

        root     = self._root()
        sketch   = root.sketches.add(root.xYConstructionPlane)
        center   = adsk.core.Point3D.create(position[0], position[1], 0)
        sketch.sketchCurves.sketchCircles.addByCenterRadius(center, radius)

        profile  = sketch.profiles.item(0)
        ext_in   = root.features.extrudeFeatures.createInput(
            profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
        )
        ext_in.setDistanceExtent(False, adsk.core.ValueInput.createByReal(height))
        root.features.extrudeFeatures.add(ext_in)
        return {"status": "success", "message": f"Created cylinder r={radius} h={height}"}

    def _create_box(self, p) -> dict:
        length   = float(p.get("length", 1.0))
        width    = float(p.get("width",  1.0))
        height   = float(p.get("height", 1.0))
        position = p.get("position", [0, 0, 0])
        px, py   = position[0], position[1]

        root   = self._root()
        sketch = root.sketches.add(root.xYConstructionPlane)
        sketch.sketchCurves.sketchLines.addCenterPointRectangle(
            adsk.core.Point3D.create(px, py, 0),
            adsk.core.Point3D.create(px + length / 2, py + width / 2, 0),
        )
        profile = sketch.profiles.item(0)
        ext_in  = root.features.extrudeFeatures.createInput(
            profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
        )
        ext_in.setDistanceExtent(False, adsk.core.ValueInput.createByReal(height))
        root.features.extrudeFeatures.add(ext_in)
        return {"status": "success", "message": f"Created box {length}×{width}×{height}"}

    def _create_sphere(self, p) -> dict:
        radius   = float(p.get("radius", 1.0))
        position = p.get("position", [0, 0, 0])
        px, pz   = position[0], position[2]

        root   = self._root()
        sketch = root.sketches.add(root.xZConstructionPlane)
        center = adsk.core.Point3D.create(px, 0, pz)
        sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
            center,
            adsk.core.Point3D.create(px, 0, pz + radius),
            3.14159265358979,
        )
        sketch.sketchCurves.sketchLines.addByTwoPoints(
            adsk.core.Point3D.create(px, 0, pz - radius),
            adsk.core.Point3D.create(px, 0, pz + radius),
        )
        profile = sketch.profiles.item(0)
        rev_in  = root.features.revolveFeatures.createInput(
            profile,
            root.zConstructionAxis,
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        )
        rev_in.setAngleExtent(False, adsk.core.ValueInput.createByReal(2 * 3.14159265358979))
        root.features.revolveFeatures.add(rev_in)
        return {"status": "success", "message": f"Created sphere r={radius}"}

    def _get_body_list(self, p) -> dict:
        """List all bodies in the design."""
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
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

        return {'success': True, 'bodies': bodies, 'count': len(bodies)}

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
        """Execute a Python script inside Fusion 360's environment."""
        import io
        import sys
        import traceback as tb

        script_code = p.get('script', '')
        timeout = p.get('timeout', 30)  # seconds (noted: not easily enforced in F360)

        if not script_code.strip():
            return {'status': 'error', 'success': False, 'error': 'Empty script', 'stdout': '', 'stderr': ''}

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
            exec_globals = {
                '__builtins__': __builtins__,
                'adsk': adsk,
                'app': app,
                'design': design,
                'rootComp': design.rootComponent if design else None,
                'ui': app.userInterface,
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

        return {
            'status': 'success' if success else 'error',
            'success': success,
            'stdout': captured_out.getvalue(),
            'stderr': captured_err.getvalue(),
            'error': error_msg,
            'result': str(result_value) if result_value is not None else None
        }

    def _undo(self, p) -> dict:
        self._app.executeTextCommand("Commands.Undo")
        return {"status": "success", "message": "Undo performed."}

    def _save_document(self, p) -> dict:
        doc = self._app.activeDocument
        if not doc:
            return {"status": "error", "message": "No active document."}
        doc.save("Saved by Fusion 360 MCP")
        return {"status": "success", "message": "Document saved."}

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
        """Find a body by name in all components. Returns the body or raises RuntimeError."""
        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            raise RuntimeError("No active design.")
        for comp in design.allComponents:
            for i in range(comp.bRepBodies.count):
                body = comp.bRepBodies.item(i)
                if body.name == body_name:
                    return body
        raise RuntimeError(f"Body '{body_name}' not found.")

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
        feature = root.features.extrudeFeatures.add(ext_input)

        body_name = ""
        if feature.bodies.count > 0:
            body_name = feature.bodies.item(0).name

        return {
            "status": "success",
            "success": True,
            "feature_name": feature.name,
            "body_name": body_name,
        }

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

    def _handle_export_stl(self, p) -> dict:
        import os

        filename = p.get("filename", "export.stl")
        body_name = p.get("body_name")
        refinement = p.get("refinement", "medium").lower()

        design = adsk.fusion.Design.cast(self._app.activeProduct)
        if not design:
            return {"status": "error", "message": "No active design."}

        export_mgr = design.exportManager

        stl_opts = export_mgr.createSTLExportOptions(design.rootComponent)

        # If a specific body is requested, find it
        if body_name:
            body = self._find_body(body_name)
            stl_opts = export_mgr.createSTLExportOptions(body)

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

        filename = p.get("filename", "export.step")

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

        filename = p.get("filename", "export.f3d")

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
        try:
            body = self._find_body(body_name)
        except RuntimeError:
            return {'success': False, 'error': f'Body "{body_name}" not found'}

        phys = body.physicalProperties
        bb = body.boundingBox

        return {
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
        self._app.executeTextCommand("Commands.Redo")
        return {"status": "success", "message": "Redo performed."}

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

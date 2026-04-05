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
            "undo":              self._undo,
            "save_document":     self._save_document,
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

    def _undo(self, p) -> dict:
        self._app.executeTextCommand("Commands.Undo")
        return {"status": "success", "message": "Undo performed."}

    def _save_document(self, p) -> dict:
        doc = self._app.activeDocument
        if not doc:
            return {"status": "error", "message": "No active document."}
        doc.save("Saved by Fusion 360 MCP")
        return {"status": "success", "message": "Document saved."}

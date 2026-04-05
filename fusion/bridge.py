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

    def undo(self) -> dict[str, Any]:
        if self.simulation_mode:
            return self._sim("Undo performed.")
        return self._send_command("undo", {})

    def save_document(self) -> dict[str, Any]:
        if self.simulation_mode:
            return self._sim("Document saved.")
        return self._send_command("save_document", {})

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
            "undo":              lambda p: self.undo(),
            "save_document":     lambda p: self.save_document(),
        }
        handler = dispatch.get(command)
        if handler is None:
            return {"status": "error", "message": f"Unknown command: '{command}'"}
        return handler(parameters)

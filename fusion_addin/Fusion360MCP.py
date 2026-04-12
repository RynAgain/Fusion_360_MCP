"""
fusion_addin/Fusion360MCP.py
Fusion 360 Add-in entry point.

INSTALLATION:
  1. Copy the entire `fusion_addin/` folder into:
       ~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/Fusion360MCP/
  2. In Fusion 360: Tools → Add-Ins → Scripts and Add-Ins → Add-Ins tab
     → click the ▶ Run button next to "Fusion360MCP"
  3. The add-in will start a TCP server on 127.0.0.1:9876
  4. Launch the external MCP Controller app (python main.py)

The add-in stays running in the background. Stop it from the Add-Ins panel.
"""

import adsk.core
import adsk.fusion
import adsk.cam
import traceback

# Import our server module (same directory)
from . import addin_server

_handlers = []
_server   = None


def run(context):
    global _server
    ui = None
    try:
        app = adsk.core.Application.get()
        ui  = app.userInterface

        # Start the TCP command server in a background thread
        _server = addin_server.FusionCommandServer(app, ui)
        _server.start()

        ui.messageBox(
            "Artifex360 Add-in started!\n\n"
            "Listening on 127.0.0.1:9876\n"
            "You can now launch Artifex360.",
            "Artifex360"
        )

    except Exception:
        if ui:
            ui.messageBox(f"Fusion360MCP failed to start:\n{traceback.format_exc()}")


def stop(context):
    global _server
    ui = None
    try:
        app = adsk.core.Application.get()
        ui  = app.userInterface
        if _server:
            _server.stop()
            _server = None
        ui.messageBox("Artifex360 Add-in stopped.", "Artifex360")
    except Exception:
        if ui:
            ui.messageBox(f"Error stopping add-in:\n{traceback.format_exc()}")

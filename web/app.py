"""
web/app.py
Flask application factory with Socket.IO integration.

Creates and configures the Flask app, initializes shared components
(FusionBridge, MCPServer, ClaudeClient), and wires up routes + events.
"""

import logging
import os

from flask import Flask
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared component instances — accessible from routes and events modules
# ---------------------------------------------------------------------------
bridge = None           # FusionBridge instance
mcp_server = None       # MCPServer instance
claude_client = None    # ClaudeClient instance
socketio_instance = None  # SocketIO instance


def _detect_async_mode() -> str:
    """
    Detect the active async runtime.

    Reads the environment variable set by main.py.  If not present
    (e.g. when imported from tests), probes for installed packages
    and returns the best available mode.
    """
    env_mode = os.environ.get("FUSION_MCP_ASYNC_MODE")
    if env_mode:
        return env_mode
    # Fallback: probe installed packages
    try:
        import eventlet  # noqa: F401
        return "eventlet"
    except ImportError:
        pass
    try:
        import gevent  # noqa: F401
        return "gevent"
    except ImportError:
        pass
    return "threading"


def create_app() -> tuple[Flask, SocketIO]:
    """
    Flask application factory.

    Returns:
        (app, socketio) tuple ready for socketio.run().
    """
    global bridge, mcp_server, claude_client, socketio_instance

    # Resolve template and static paths relative to *this* file
    web_dir = os.path.dirname(os.path.abspath(__file__))
    template_dir = os.path.join(web_dir, "templates")
    static_dir = os.path.join(web_dir, "static")

    app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
    )
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "fusion-mcp-dev-key")

    # ----- Socket.IO ---------------------------------------------------
    async_mode = _detect_async_mode()
    logger.info("SocketIO async_mode = %s", async_mode)

    socketio = SocketIO(
        app,
        async_mode=async_mode,
        cors_allowed_origins="*",
        logger=False,
        engineio_logger=False,
    )
    socketio_instance = socketio

    # ----- Shared components -------------------------------------------
    from config.settings import settings
    from fusion.bridge import FusionBridge
    from mcp.server import MCPServer
    from ai.claude_client import ClaudeClient

    bridge = FusionBridge(simulation_mode=settings.simulation_mode)
    mcp_server = MCPServer(bridge)
    claude_client = ClaudeClient(settings, mcp_server)

    logger.info("Shared components initialised (simulation_mode=%s)", bridge.simulation_mode)

    # ----- Register REST blueprint -------------------------------------
    from web.routes import api as api_blueprint
    app.register_blueprint(api_blueprint)

    # ----- Register Socket.IO event handlers ---------------------------
    from web import events as _events_module  # noqa: F841 — registration happens at import time
    _events_module.register(socketio)

    logger.info("Flask app created — routes and events registered")
    return app, socketio

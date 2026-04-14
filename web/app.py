"""
web/app.py
Flask application factory with Socket.IO integration.

Creates and configures the Flask app, initializes shared components
(FusionBridge, MCPServer, ClaudeClient), and wires up routes + events.
"""

import logging
import os
import secrets

from flask import Flask, current_app
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TASK-036: Accessor functions for shared components stored on app.extensions
# ---------------------------------------------------------------------------

def get_bridge():
    """Return the FusionBridge instance from the current Flask app."""
    return current_app.extensions['fusion_bridge']


def get_mcp_server():
    """Return the MCPServer instance from the current Flask app."""
    return current_app.extensions['mcp_server']


def get_claude_client():
    """Return the ClaudeClient instance from the current Flask app."""
    return current_app.extensions['claude_client']


def get_socketio():
    """Return the SocketIO instance from the current Flask app."""
    return current_app.extensions['socketio_instance']


# ---------------------------------------------------------------------------
# Module-level references kept for backward compatibility during transition.
# TASK-036: New code should use the accessor functions above.
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
    env_mode = os.environ.get("ARTIFEX360_ASYNC_MODE")
    if env_mode:
        return env_mode
    # Fallback: probe installed packages
    try:
        import eventlet  # noqa: F401
        return "eventlet"
    except Exception:
        pass
    try:
        import gevent  # noqa: F401
        return "gevent"
    except Exception:
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

    # Security: never use a hardcoded secret key.  Priority:
    #   1. SECRET_KEY env var
    #   2. Persisted random key in data/.secret_key (survives restarts)
    #   3. Generate + persist a new random key
    secret_key = os.environ.get("SECRET_KEY", "")
    if not secret_key:
        _secret_key_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", ".secret_key",
        )
        try:
            if os.path.exists(_secret_key_path):
                with open(_secret_key_path, "r", encoding="utf-8") as f:
                    secret_key = f.read().strip()
        except OSError:
            pass
        if not secret_key:
            secret_key = secrets.token_hex(32)
            try:
                os.makedirs(os.path.dirname(_secret_key_path), exist_ok=True)
                with open(_secret_key_path, "w", encoding="utf-8") as f:
                    f.write(secret_key)
                logger.info("Generated and persisted new Flask secret key at %s", _secret_key_path)
            except OSError as exc:
                logger.warning("Could not persist secret key: %s", exc)
    app.config["SECRET_KEY"] = secret_key

    # ----- Socket.IO ---------------------------------------------------
    async_mode = _detect_async_mode()
    logger.info("SocketIO async_mode = %s", async_mode)

    # Security: restrict CORS origins instead of wildcard "*".
    # Reads from CORS_ORIGINS env var; defaults to localhost only.
    cors_origins = os.environ.get(
        "CORS_ORIGINS", "http://127.0.0.1:*,http://localhost:*"
    ).split(",")
    cors_origins = [o.strip() for o in cors_origins if o.strip()]

    socketio = SocketIO(
        app,
        async_mode=async_mode,
        cors_allowed_origins=cors_origins,
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

    # TASK-036: Store shared components on the Flask app object
    app.extensions['fusion_bridge'] = bridge
    app.extensions['mcp_server'] = mcp_server
    app.extensions['claude_client'] = claude_client
    app.extensions['socketio_instance'] = socketio

    logger.info("Shared components initialised (simulation_mode=%s)", bridge.simulation_mode)

    # ----- Create example rule files -----------------------------------
    from ai.rules_loader import create_example_rules
    try:
        create_example_rules()
    except Exception as exc:
        logger.warning("Failed to create example rule files: %s", exc)

    # ----- Register REST blueprint -------------------------------------
    from web.routes import api as api_blueprint
    app.register_blueprint(api_blueprint)

    # ----- Register Socket.IO event handlers ---------------------------
    from web import events as _events_module  # noqa: F841 — registration happens at import time
    _events_module.register(socketio)

    logger.info("Flask app created — routes and events registered")
    return app, socketio

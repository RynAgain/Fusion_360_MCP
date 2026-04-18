"""
web/app.py
Flask application factory with Socket.IO integration.

Creates and configures the Flask app, initializes shared components
(FusionBridge, MCPServer, ClaudeClient), and wires up routes + events.
"""

import logging
import os
import secrets
import stat

from flask import Flask, current_app
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TASK-036: Accessor functions for shared components stored on app.extensions
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Module-level references kept for backward compatibility during transition.
# ---------------------------------------------------------------------------
# TODO: TASK-107 migration path:
# 1. Audit all `from web.app import bridge` usages across the codebase.
# 2. Replace each with `from web.app import get_bridge` (or use app.extensions).
# 3. Once no direct imports remain, remove the globals and __getattr__ shim below.
# 4. The __getattr__ shim below provides backward compatibility during transition
#    by resolving module-level attribute access to app.extensions when inside a
#    request context, falling back to the module globals for startup / non-request use.
bridge = None           # FusionBridge instance
mcp_server = None       # MCPServer instance
claude_client = None    # ClaudeClient instance
socketio_instance = None  # SocketIO instance


def __getattr__(name):
    """Module-level __getattr__ for backward compatibility (TASK-107).

    When code does ``from web.app import bridge``, Python resolves the
    module attribute.  During a request context the value is pulled from
    ``current_app.extensions`` so it always reflects the live app state.
    Outside a request context (e.g. startup, tests) the module-level
    global is returned as a fallback.
    """
    _mapping = {
        'bridge': 'fusion_bridge',
        'mcp_server': 'mcp_server',
        'claude_client': 'claude_client',
        'socketio_instance': 'socketio_instance',
    }
    if name in _mapping:
        try:
            return current_app.extensions[_mapping[name]]
        except (RuntimeError, KeyError):
            # Outside request context or extension not registered yet --
            # fall back to the module-level global.
            return globals().get(f"_{name}_fallback")
    raise AttributeError(f"module 'web.app' has no attribute {name!r}")


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
                # TASK-130: Restrict secret key file permissions (best-effort on Windows)
                try:
                    os.chmod(_secret_key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
                except OSError:
                    pass  # Best-effort on Windows
                logger.info("Generated and persisted new Flask secret key at %s", _secret_key_path)
            except OSError as exc:
                logger.warning("Could not persist secret key: %s", exc)
    app.config["SECRET_KEY"] = secret_key

    # ----- Socket.IO ---------------------------------------------------
    async_mode = _detect_async_mode()
    logger.info("SocketIO async_mode = %s", async_mode)

    # Security: restrict CORS origins instead of wildcard "*".
    # Reads from CORS_ORIGINS env var; defaults to localhost only.
    # Note: engine.io does not expand glob patterns like "localhost:*" —
    # we build explicit origins for the configured port instead.
    port = int(os.environ.get("PORT", 8080))
    default_origins = (
        f"http://127.0.0.1:{port},"
        f"http://localhost:{port},"
        "http://127.0.0.1:5000,"
        "http://localhost:5000"
    )
    cors_origins = os.environ.get("CORS_ORIGINS", default_origins).split(",")
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
    from fusion.bridge import FusionBridge
    from mcp.server import MCPServer
    from ai.claude_client import ClaudeClient
    from config.settings import settings

    bridge = FusionBridge()

    # Auto-connect: Try to reach the Fusion 360 addin
    try:
        result = bridge.connect()
        logger.info("Fusion 360 addin connection: %s", result.get('status', 'unknown'))
    except Exception as e:
        logger.info("Fusion 360 addin not available: %s. Connect manually when ready.", e)

    mcp_server = MCPServer(bridge)
    claude_client = ClaudeClient(settings, mcp_server)

    # TASK-036: Store shared components on the Flask app object
    app.extensions['fusion_bridge'] = bridge
    app.extensions['mcp_server'] = mcp_server
    app.extensions['claude_client'] = claude_client
    app.extensions['socketio_instance'] = socketio

    logger.info("Shared components initialised (connected=%s)", bridge.connected)

    # ----- Create example rule files -----------------------------------
    from ai.rules_loader import create_example_rules
    try:
        create_example_rules()
    except Exception as exc:
        logger.warning("Failed to create example rule files: %s", exc)

    # ----- Register REST blueprint -------------------------------------
    from web.routes import api as api_blueprint
    app.register_blueprint(api_blueprint)

    # ----- TASK-053: Localhost / API token authentication ---------------
    # For /api/ routes, ensure the request originates from localhost OR
    # carries a valid Bearer token (if API_TOKEN env var is set).
    # Skip auth for: GET /, GET /static/*, Socket.IO handshake paths.
    _LOCALHOST_ADDRS = {"127.0.0.1", "::1", "localhost"}
    _api_token = os.environ.get("API_TOKEN", "").strip() or None

    @app.before_request
    def _auth_and_csrf_check():
        from flask import request, jsonify

        path = request.path

        # --- Skip auth for public routes and Socket.IO handshake ---
        if path == "/" or path.startswith("/static/") or path.startswith("/socket.io"):
            return None

        # --- TASK-053: Localhost / token gate for /api/ routes ---
        if path.startswith("/api/"):
            remote = request.remote_addr or ""
            is_local = remote in _LOCALHOST_ADDRS

            if _api_token:
                # When API_TOKEN is configured, require either localhost
                # OR a valid Bearer token.
                auth_header = request.headers.get("Authorization", "")
                has_valid_token = (
                    auth_header.startswith("Bearer ")
                    and auth_header[7:] == _api_token
                )
                if not is_local and not has_valid_token:
                    logger.warning(
                        "TASK-053: Rejected request from %s to %s (not local, no valid token)",
                        remote, path,
                    )
                    return jsonify({"error": "Unauthorized"}), 401
            else:
                # No API_TOKEN set -- restrict to localhost only
                if not is_local:
                    logger.warning(
                        "TASK-053: Rejected non-local request from %s to %s",
                        remote, path,
                    )
                    return jsonify({"error": "Unauthorized"}), 401

            # --- TASK-047: CSRF protection for state-changing requests ---
            # Strategy: Require the ``X-Requested-With: XMLHttpRequest`` header on
            # all state-changing requests (POST / PUT / DELETE) to ``/api/`` routes.
            #
            # Why this works: The ``X-Requested-With`` header is a *custom* header.
            # Browsers will not attach custom headers on cross-origin requests
            # unless the server explicitly grants permission via a CORS preflight
            # response (``Access-Control-Allow-Headers``).  Since our CORS policy
            # only allows our own origin, a malicious site cannot forge this header
            # in a cross-origin request, which blocks CSRF attacks.
            if request.method in ("POST", "PUT", "DELETE"):
                if request.headers.get("X-Requested-With") != "XMLHttpRequest":
                    return jsonify({"error": "Missing or invalid X-Requested-With header"}), 403

    # ----- Register Socket.IO event handlers ---------------------------
    from web import events as _events_module  # noqa: F841 — registration happens at import time
    _events_module.register(socketio)

    logger.info("Flask app created — routes and events registered")
    return app, socketio

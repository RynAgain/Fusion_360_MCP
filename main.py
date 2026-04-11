#!/usr/bin/env python3
"""
main.py
Fusion 360 MCP Agent -- entry point.

Run with:
    python main.py

Requirements:
    pip install -r requirements.txt
"""

# ---------------------------------------------------------------------------
# Async runtime selection -- MUST happen before any other imports.
# Tries eventlet first (best Socket.IO perf on Linux / Windows / Intel Mac),
# falls back to gevent (recommended for macOS Apple Silicon / ARM64),
# and finally to plain threading (always available, slower).
# ---------------------------------------------------------------------------
import sys
import os
import platform

ASYNC_MODE: str | None = None

try:
    import eventlet
    eventlet.monkey_patch()
    ASYNC_MODE = "eventlet"
except Exception:
    try:
        from gevent import monkey
        monkey.patch_all()
        ASYNC_MODE = "gevent"
    except Exception:
        ASYNC_MODE = "threading"

# Store in environment so web/app.py can read it without circular imports
os.environ["FUSION_MCP_ASYNC_MODE"] = ASYNC_MODE

import logging

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so all packages resolve correctly
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Logging setup -- writes to console + fusion_mcp.log
# ---------------------------------------------------------------------------
LOG_FILE = os.path.join(PROJECT_ROOT, "fusion_mcp.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s -- %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

from ai.log_sanitizer import add_sanitizer_to_logging
add_sanitizer_to_logging()

logger = logging.getLogger(__name__)


def check_python_version():
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10 or higher is required.")
        print(f"       You are running Python {sys.version}")
        sys.exit(1)


def check_dependencies():
    """Warn about missing optional packages without hard-failing."""
    missing = []
    try:
        import anthropic  # noqa: F401
    except ImportError:
        missing.append("anthropic")
    try:
        import flask  # noqa: F401
    except ImportError:
        missing.append("Flask")
    try:
        import flask_socketio  # noqa: F401
    except ImportError:
        missing.append("Flask-SocketIO")

    if missing:
        print("[!] Missing packages detected:")
        for pkg in missing:
            print(f"    pip install {pkg}")
        print("    (Install them with: pip install -r requirements.txt)\n")


def main():
    check_python_version()
    check_dependencies()

    # Load environment variables from .env file if present
    try:
        from dotenv import load_dotenv
        load_dotenv()
        logger.info("Loaded .env file (if present)")
    except ImportError:
        pass  # python-dotenv not installed; environment variables still work

    from config.settings import settings

    logger.info("=" * 60)
    logger.info("Fusion 360 MCP Agent Starting")
    logger.info("  Version:    1.1.0")
    logger.info("  Platform:   %s %s", platform.system(), platform.machine())
    logger.info("  Python:     %s", platform.python_version())
    logger.info("  Async Mode: %s", ASYNC_MODE)
    logger.info("  Provider:   %s", settings.provider)
    logger.info("  Simulation: %s", settings.simulation_mode)
    logger.info("=" * 60)

    try:
        from web.app import create_app
    except ImportError as exc:
        logger.exception("Failed to import web application")
        print(f"\nFATAL: Could not import web app: {exc}")
        print("Make sure Flask and Flask-SocketIO are installed:")
        print("  pip install -r requirements.txt")
        sys.exit(1)

    app, socketio = create_app()

    port = int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "0.0.0.0")

    logger.info("  Port:       %s", port)
    print(f"Starting Fusion 360 MCP Agent at http://localhost:{port}")
    logger.info("Listening on %s:%s", host, port)

    # Disable the Werkzeug debug reloader when using gevent — the reloader
    # forks a child process which breaks gevent's monkey-patching and causes
    # the child to crash silently (AssertionError in gevent.threading).
    use_reloader = ASYNC_MODE != "gevent"
    socketio.run(app, host=host, port=port, debug=True, use_reloader=use_reloader, allow_unsafe_werkzeug=True)

    logger.info("Application exited cleanly.")


if __name__ == "__main__":
    main()

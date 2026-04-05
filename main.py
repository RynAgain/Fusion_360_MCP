#!/usr/bin/env python3
"""
main.py
Fusion 360 MCP Controller — entry point.

Run with:
    python main.py

Requirements:
    pip install -r requirements.txt
"""

import sys
import os
import logging

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so all packages resolve correctly
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Logging setup — writes to console + fusion_mcp.log
# ---------------------------------------------------------------------------
LOG_FILE = os.path.join(PROJECT_ROOT, "fusion_mcp.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
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

    if missing:
        print("⚠️  Missing packages detected:")
        for pkg in missing:
            print(f"   pip install {pkg}")
        print("   (You can still launch the UI and install them later.)\n")


def main():
    check_python_version()
    check_dependencies()

    logger.info("Starting Fusion 360 MCP Controller…")

    # Import here so logging is configured first
    try:
        from ui.app import App
    except ImportError as exc:
        logger.exception("Failed to import UI — is tkinter installed?")
        print(f"\nFATAL: Could not import UI: {exc}")
        print("Make sure tkinter is available (it ships with standard Python on macOS).")
        sys.exit(1)

    app = App()
    logger.info("UI launched — entering main loop.")
    app.mainloop()
    logger.info("Application exited cleanly.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scripts/install_addin.py
Artifex360 -- Cross-platform Fusion 360 add-in installer.

Usage:
    python scripts/install_addin.py            # install
    python scripts/install_addin.py uninstall  # remove
"""

import os
import sys
import shutil
import platform


def get_addin_directory() -> str:
    """Get the Fusion 360 AddIns directory for the current platform."""
    system = platform.system()

    if system == "Darwin":  # macOS
        home = os.path.expanduser("~")
        return os.path.join(
            home, "Library", "Application Support", "Autodesk",
            "Autodesk Fusion 360", "API", "AddIns",
        )
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            appdata = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(
            appdata, "Autodesk", "Autodesk Fusion 360", "API", "AddIns",
        )
    else:
        print(f"[error] Unsupported platform: {system}")
        sys.exit(1)


def install() -> None:
    """Copy the fusion_addin directory to Fusion 360's AddIns directory."""
    # Source directory
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = os.path.join(project_root, "fusion_addin")

    if not os.path.exists(source):
        print(f"[error] Source directory not found: {source}")
        sys.exit(1)

    # Destination
    addins_dir = get_addin_directory()
    dest = os.path.join(addins_dir, "Fusion360MCP")

    print(f"Platform : {platform.system()} ({platform.machine()})")
    print(f"Source   : {source}")
    print(f"Target   : {dest}")
    print()

    # Create AddIns directory if it doesn't exist
    os.makedirs(addins_dir, exist_ok=True)

    # Remove existing installation (with confirmation)
    if os.path.exists(dest):
        print(f"Existing installation found at: {dest}")
        confirm = input("Remove and reinstall? [y/N] ").strip().lower()
        if confirm != 'y':
            print("Aborted.")
            sys.exit(0)
        print("Removing existing installation...")
        shutil.rmtree(dest)

    # Copy
    print("Installing add-in...")
    shutil.copytree(source, dest)

    print()
    print("[ok] Artifex360 add-in installed successfully!")
    print("     Restart Fusion 360 and enable 'Fusion360MCP' from Scripts and Add-Ins (Shift+S).")


def uninstall() -> None:
    """Remove the add-in from Fusion 360's AddIns directory."""
    addins_dir = get_addin_directory()
    dest = os.path.join(addins_dir, "Fusion360MCP")

    if os.path.exists(dest):
        shutil.rmtree(dest)
        print(f"[ok] Add-in removed from {dest}")
    else:
        print("[--] Add-in not found. Nothing to remove.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
        uninstall()
    else:
        install()

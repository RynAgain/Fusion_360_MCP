# Pip Package Plan -- Artifex360

## Overview

Package Artifex360 as an installable Python package (`pip install artifex360`) with CLI entry points for the server and the Fusion 360 add-in installer.

---

## Target User Experience

```bash
# Install
pip install artifex360

# Install the Fusion 360 add-in (one-time)
artifex360 install-addin

# Start the server
artifex360 serve

# Or as a module
python -m artifex360
```

First launch opens browser to `http://localhost:5000` with a setup wizard for API key configuration.

---

## Package Structure

```
Fusion_360_MCP/
  pyproject.toml              # Build config (hatchling)
  README.md                   # PyPI long description
  LICENSE                     # MIT or chosen license
  src/
    artifex360/
      __init__.py             # Version, package metadata
      __main__.py             # python -m artifex360 entry
      cli.py                  # Click CLI: serve, install-addin, doctor
      app.py                  # Re-export from web.app (create_app)
      ai/                     # Moved from top-level ai/
      config/                 # Moved from top-level config/
      fusion/                 # Moved from top-level fusion/
      mcp/                    # Moved from top-level mcp/
      web/                    # Moved from top-level web/
      fusion_addin/           # Add-in source (copied to Fusion directory)
      data/                   # Default data templates (.gitkeep files)
      docs/                   # Skill documents bundled as package data
  tests/                      # Stays at top level (not packaged)
```

> **Alternative (simpler):** Keep the flat layout, add `pyproject.toml` at root, use `packages = ["ai", "config", "fusion", "mcp", "web"]` in hatch config. Avoids the massive rename.

---

## Phase 1: pyproject.toml (Minimal Change)

Keep the current flat layout. No source move needed.

```toml
[project]
name = "artifex360"
version = "1.9.0"
description = "AI-powered CAD design assistant for Autodesk Fusion 360"
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
authors = [
    { name = "Your Name", email = "you@example.com" },
]
keywords = ["fusion360", "cad", "ai", "mcp", "3d-printing"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Web Environment",
    "Framework :: Flask",
    "Intended Audience :: Developers",
    "Intended Audience :: Manufacturing",
    "License :: OSI Approved :: MIT License",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: MacOS",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Scientific/Engineering :: Interface Engine/Protocol Translator",
]

dependencies = [
    "flask>=3.0",
    "flask-socketio>=5.3",
    "eventlet>=0.36",
    "anthropic>=0.39",
    "httpx>=0.27",
    "python-dotenv>=1.0",
    "werkzeug>=3.0",
]

[project.optional-dependencies]
ollama = []  # No extra deps needed (uses httpx)
dev = [
    "pytest>=7.0",
    "pytest-cov",
]
docs = [
    "pymupdf>=1.23",   # PDF extraction
    "python-docx",      # DOCX extraction
]

[project.scripts]
artifex360 = "main:cli_main"

[project.urls]
Homepage = "https://github.com/youruser/Fusion_360_MCP"
Repository = "https://github.com/youruser/Fusion_360_MCP"
Issues = "https://github.com/youruser/Fusion_360_MCP/issues"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["ai", "config", "fusion", "fusion_addin", "mcp", "web"]
# Include non-Python files
artifacts = [
    "web/templates/**",
    "web/static/**",
    "config/locales/**",
    "config/rules/**",
    "config/rules-orchestrator/**",
    "config/rules-sketch/**",
    "config/custom_modes.json",
    "docs/F360_SKILL.md",
    "fusion_addin/*.py",
    "fusion_addin/*.manifest",
]

[tool.hatch.build.targets.sdist]
include = [
    "ai/",
    "config/",
    "fusion/",
    "fusion_addin/",
    "mcp/",
    "web/",
    "main.py",
    "requirements.txt",
    "docs/F360_SKILL.md",
]
```

---

## Phase 2: CLI Entry Point

### `main.py` additions:

```python
import click

@click.group(invoke_without_command=True)
@click.pass_context
def cli_main(ctx):
    """Artifex360 -- AI-powered CAD assistant for Fusion 360."""
    if ctx.invoked_subcommand is None:
        # Default: start the server
        ctx.invoke(serve)

@cli_main.command()
@click.option("--host", default="127.0.0.1", help="Server host")
@click.option("--port", default=5000, type=int, help="Server port")
@click.option("--open-browser/--no-browser", default=True)
def serve(host, port, open_browser):
    """Start the Artifex360 web server."""
    from web.app import create_app, socketio
    import webbrowser

    app = create_app()
    if open_browser:
        import threading
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    socketio.run(app, host=host, port=port)

@cli_main.command()
def install_addin():
    """Install the Fusion 360 add-in to the system add-ins directory."""
    from scripts.install_addin import main as install_main
    install_main()

@cli_main.command()
def doctor():
    """Check system prerequisites and configuration."""
    import sys
    print(f"Python: {sys.version}")
    print(f"Platform: {sys.platform}")

    # Check Fusion 360 add-in directory
    from scripts.install_addin import get_addin_dir
    addin_dir = get_addin_dir()
    print(f"Add-in directory: {addin_dir}")
    print(f"Add-in installed: {addin_dir.exists()}")

    # Check API key
    from config.settings import settings
    has_key = bool(settings.api_key and not settings.api_key.startswith("sk-ant-"))
    print(f"Anthropic API key configured: {'Yes' if has_key else 'No -- set in UI or .env'}")

    # Check Ollama
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=2)
        print(f"Ollama: Running ({r.status_code})")
    except Exception:
        print("Ollama: Not running (optional)")
```

---

## Phase 3: Data Directory Handling

The app writes to `data/` (conversations, uploads, design states). When installed via pip, the package directory is read-only. Solution:

```python
# config/settings.py -- add data_dir resolution
import os
from pathlib import Path

def get_data_dir() -> Path:
    """Resolve the writable data directory.

    Priority:
    1. ARTIFEX360_DATA_DIR environment variable
    2. ./data/ (if writable -- dev mode)
    3. ~/.artifex360/data/ (installed mode)
    """
    env_dir = os.environ.get("ARTIFEX360_DATA_DIR")
    if env_dir:
        return Path(env_dir)

    local_data = Path("data")
    if local_data.exists() and os.access(local_data, os.W_OK):
        return local_data

    home_dir = Path.home() / ".artifex360" / "data"
    home_dir.mkdir(parents=True, exist_ok=True)
    return home_dir
```

This keeps development unchanged (`./data/`) while supporting installed mode (`~/.artifex360/data/`).

---

## Phase 4: Fusion Add-in Installer

### `scripts/install_addin.py` (enhanced):

```python
import platform
import shutil
from pathlib import Path

def get_addin_dir() -> Path:
    """Get the Fusion 360 add-ins directory for the current platform."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", ""))
        return base / "Autodesk" / "Autodesk Fusion 360" / "API" / "AddIns" / "Fusion360MCP"
    elif platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Autodesk" / \
               "Autodesk Fusion 360" / "API" / "AddIns" / "Fusion360MCP"
    else:
        raise RuntimeError("Fusion 360 is only supported on Windows and macOS")

def main():
    """Copy the Fusion 360 add-in to the system add-ins directory."""
    # Find the add-in source (works both in dev and installed mode)
    source = Path(__file__).parent.parent / "fusion_addin"
    if not source.exists():
        # Installed mode: look in package data
        import importlib.resources
        source = Path(importlib.resources.files("fusion_addin"))

    dest = get_addin_dir()
    dest.mkdir(parents=True, exist_ok=True)

    files_to_copy = [
        "Fusion360MCP.py",
        "Fusion360MCP.manifest",
        "addin_server.py",
        "__init__.py",
    ]

    for fname in files_to_copy:
        src_file = source / fname
        if src_file.exists():
            shutil.copy2(src_file, dest / fname)

    print(f"Add-in installed to: {dest}")
    print()
    print("Next steps:")
    print("  1. Open Fusion 360")
    print("  2. Go to Tools > Add-Ins (Shift+S)")
    print("  3. Click 'Add-Ins' tab")
    print("  4. Find 'Fusion360MCP' and click 'Run'")
    print("  5. Check 'Run on Startup' for auto-start")
```

---

## Phase 5: First-Run Setup Wizard

Add a route that detects missing API key and shows configuration UI:

```python
# web/routes.py
@api.route("/api/setup-status")
def setup_status():
    """Check if initial setup is complete."""
    from config.settings import settings
    return jsonify({
        "needs_setup": not bool(settings.api_key),
        "has_fusion": bridge.connected,
        "has_ollama": _check_ollama(),
    })
```

The frontend shows a setup modal on first load if `needs_setup` is true.

---

## Phase 6: Publishing to PyPI

### Build and upload:

```bash
# Install build tools
pip install build twine

# Build
python -m build

# Check the package
twine check dist/*

# Upload to TestPyPI first
twine upload --repository testpypi dist/*

# Install from TestPyPI to verify
pip install --index-url https://test.pypi.org/simple/ artifex360

# Upload to production PyPI
twine upload dist/*
```

### GitHub Actions (automated release):

```yaml
# .github/workflows/release.yml
name: Release to PyPI
on:
  release:
    types: [published]
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write  # Trusted publishing
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install build
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
```

---

## Migration Checklist

| Step | Description | Breaking? |
|------|-------------|-----------|
| 1 | Add `pyproject.toml` | No |
| 2 | Add `click` to dependencies + CLI in `main.py` | No (existing `main.py` still works) |
| 3 | Add `get_data_dir()` to settings | No (falls back to `./data/`) |
| 4 | Enhance `scripts/install_addin.py` | No |
| 5 | Add `__main__.py` for `python -m artifex360` | No |
| 6 | Add setup wizard route + frontend | No |
| 7 | Test `pip install -e .` locally | No |
| 8 | First TestPyPI release | No |
| 9 | (Future) Move to `src/` layout | Yes -- requires import path updates |

---

## Dependencies Summary

### Required (core):
```
flask>=3.0
flask-socketio>=5.3
eventlet>=0.36
anthropic>=0.39
httpx>=0.27
python-dotenv>=1.0
werkzeug>=3.0
click>=8.0
```

### Optional (extras):
```
[docs]        pymupdf, python-docx
[dev]         pytest, pytest-cov
```

### System requirements:
- Python 3.11+
- Autodesk Fusion 360 (Windows or macOS)
- Anthropic API key (or Ollama for local inference)

---

## Timeline Estimate

| Phase | Effort | Notes |
|-------|--------|-------|
| Phase 1: pyproject.toml | 1 hour | Straightforward metadata |
| Phase 2: CLI entry point | 2 hours | Add click, wire up commands |
| Phase 3: Data directory | 1 hour | Settings refactor |
| Phase 4: Add-in installer | 1 hour | Mostly exists already |
| Phase 5: Setup wizard | 3 hours | Frontend + backend |
| Phase 6: PyPI publish | 1 hour | Build + upload |
| **Total** | **~9 hours** | |

---

## Open Questions

1. **Package name availability:** Check `pip search artifex360` / PyPI before committing to the name
2. **License choice:** MIT vs Apache 2.0 vs proprietary
3. **Minimum Fusion version:** Which Fusion 360 API versions are tested?
4. **macOS support status:** Currently tested? The add-in server uses `localhost` which works cross-platform, but paths differ
5. **Do we want `src/` layout?** Cleaner packaging but requires import path refactoring across all files. Can be deferred.

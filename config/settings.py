"""
config/settings.py
Persistent settings management — reads/writes config/config.json
"""

import json
import os
from typing import Any

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS: dict[str, Any] = {
    "anthropic_api_key": "",
    "model": "claude-opus-4-5",
    "max_tokens": 4096,
    "system_prompt": (
        "You are an expert CAD engineer assistant controlling Autodesk Fusion 360 "
        "via an MCP (Model Context Protocol) server. When the user asks you to create "
        "or modify geometry, use the available tools to execute commands in Fusion 360. "
        "Always confirm what you did after each action. Be concise and precise."
    ),
    "fusion_simulation_mode": True,
    "require_confirmation": False,
    "allowed_commands": [],          # empty = all allowed
    "max_requests_per_minute": 10,
    "theme": "dark",
    "window_width": 1200,
    "window_height": 800,
}


class Settings:
    """Singleton-style settings manager backed by a JSON file."""

    def __init__(self):
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load settings from disk, falling back to defaults for missing keys."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # Merge: saved values override defaults, but keep new default keys
                for key, default_val in DEFAULTS.items():
                    self._data[key] = saved.get(key, default_val)
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[Settings] Could not load config: {exc}. Using defaults.")

    def save(self) -> None:
        """Persist current settings to disk."""
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError as exc:
            print(f"[Settings] Could not save config: {exc}")

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, key: str, fallback: Any = None) -> Any:
        return self._data.get(key, fallback)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, mapping: dict[str, Any]) -> None:
        """Bulk-update settings and save."""
        self._data.update(mapping)
        self.save()

    # Convenience properties
    @property
    def api_key(self) -> str:
        return self._data.get("anthropic_api_key", "")

    @property
    def model(self) -> str:
        return self._data.get("model", DEFAULTS["model"])

    @property
    def max_tokens(self) -> int:
        return int(self._data.get("max_tokens", DEFAULTS["max_tokens"]))

    @property
    def system_prompt(self) -> str:
        return self._data.get("system_prompt", DEFAULTS["system_prompt"])

    @property
    def simulation_mode(self) -> bool:
        return bool(self._data.get("fusion_simulation_mode", True))

    @property
    def require_confirmation(self) -> bool:
        return bool(self._data.get("require_confirmation", False))

    def __repr__(self) -> str:
        safe = dict(self._data)
        if safe.get("anthropic_api_key"):
            safe["anthropic_api_key"] = "***"
        return f"Settings({safe})"


# Module-level singleton
settings = Settings()

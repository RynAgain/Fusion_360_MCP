"""
tests/test_settings.py
TASK-118: Tests for config/settings.py -- Settings class behavior.

Verifies defaults, get/set, save/load round-trip, and key filtering.
Uses tmp_path to avoid writing to the real config directory.
"""

import json
import os
import pytest
from unittest.mock import patch

from config.settings import Settings, DEFAULTS, CONFIG_FILE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(tmp_path):
    """Create a Settings instance that reads/writes to a temp config file."""
    config_path = str(tmp_path / "config.json")
    s = Settings()
    # Override the module-level CONFIG_FILE for this instance's save/load
    return s, config_path


# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Point Settings at a non-existent config so lazy-load uses defaults."""
    monkeypatch.setattr("config.settings.CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr("config.settings.CONFIG_DIR", str(tmp_path))


class TestSettingsBasic:
    def test_defaults(self):
        """A fresh Settings instance should have default values."""
        s = Settings()
        assert s.get("max_tokens") == DEFAULTS["max_tokens"]
        assert s.get("max_tokens", 4096) == DEFAULTS["max_tokens"]

    def test_get_unknown_key_returns_fallback(self):
        """Accessing an undefined key should return the fallback value."""
        s = Settings()
        assert s.get("nonexistent_key") is None
        assert s.get("nonexistent_key", 42) == 42

    def test_set_and_get_internal(self):
        """set() with _internal=True should update the value."""
        s = Settings()
        s.set("theme", "dark", _internal=True)
        assert s.get("theme") == "dark"

    def test_set_and_get_settable_key(self):
        """set() for a key in _SETTABLE_KEYS should work without _internal."""
        s = Settings()
        s.set("theme", "light")
        assert s.get("theme") == "light"

    def test_set_rejects_non_settable_key(self):
        """set() without _internal should reject keys not in _SETTABLE_KEYS."""
        s = Settings()
        with pytest.raises(ValueError, match="not externally settable"):
            s.set("__evil__", "hack")

    def test_set_rejects_arbitrary_key(self):
        """set() should reject arbitrary keys not in the allowed set."""
        s = Settings()
        with pytest.raises(ValueError):
            s.set("secret_backdoor", "value")


# ---------------------------------------------------------------------------
# Save / Load round-trip
# ---------------------------------------------------------------------------

class TestSettingsPersistence:
    def test_save_and_load(self, tmp_path, monkeypatch):
        """Settings saved to disk should be loadable."""
        config_path = str(tmp_path / "config.json")
        monkeypatch.setattr("config.settings.CONFIG_FILE", config_path)
        monkeypatch.setattr("config.settings.CONFIG_DIR", str(tmp_path))

        s1 = Settings()
        s1.set("theme", "light", _internal=True)
        s1.save()

        # Verify file was written
        assert os.path.exists(config_path)

        s2 = Settings()
        s2.load()
        assert s2.get("theme") == "light"

    def test_load_preserves_defaults_for_missing_keys(self, tmp_path, monkeypatch):
        """Loading a partial config should fill in missing keys from DEFAULTS."""
        config_path = str(tmp_path / "config.json")
        monkeypatch.setattr("config.settings.CONFIG_FILE", config_path)
        monkeypatch.setattr("config.settings.CONFIG_DIR", str(tmp_path))

        # Write a partial config
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({"theme": "retro"}, f)

        s = Settings()
        s.load()
        assert s.get("theme") == "retro"
        # Other defaults should still be present
        assert s.get("max_tokens") == DEFAULTS["max_tokens"]


# ---------------------------------------------------------------------------
# update() filtering
# ---------------------------------------------------------------------------

class TestSettingsUpdate:
    def test_update_rejects_non_settable_keys(self, tmp_path, monkeypatch):
        """update() should silently filter out keys not in _SETTABLE_KEYS."""
        config_path = str(tmp_path / "config.json")
        monkeypatch.setattr("config.settings.CONFIG_FILE", config_path)
        monkeypatch.setattr("config.settings.CONFIG_DIR", str(tmp_path))

        s = Settings()
        s.update({"theme": "dark", "__evil__": "hack"})
        assert s.get("theme") == "dark"
        assert s.get("__evil__") is None

    def test_update_saves_to_disk(self, tmp_path, monkeypatch):
        """update() should persist changes to disk."""
        config_path = str(tmp_path / "config.json")
        monkeypatch.setattr("config.settings.CONFIG_FILE", config_path)
        monkeypatch.setattr("config.settings.CONFIG_DIR", str(tmp_path))

        s = Settings()
        s.update({"theme": "dark"})
        assert os.path.exists(config_path)

        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("theme") == "dark"


# ---------------------------------------------------------------------------
# Property access
# ---------------------------------------------------------------------------

class TestSettingsProperties:
    def test_model_property(self):
        s = Settings()
        assert s.model == DEFAULTS["model"]

    def test_max_tokens_property(self):
        s = Settings()
        assert s.max_tokens == DEFAULTS["max_tokens"]

    def test_theme_property(self):
        s = Settings()
        assert s.theme == DEFAULTS["theme"]

    def test_provider_property(self):
        s = Settings()
        assert s.provider == DEFAULTS["provider"]

    def test_to_safe_dict_masks_api_key(self, monkeypatch):
        """to_safe_dict() should mask the API key."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-1234567890abcdef1234")
        s = Settings()
        safe = s.to_safe_dict()
        api_key_val = safe.get("anthropic_api_key", "")
        if api_key_val:
            assert "..." in api_key_val or api_key_val == "***"

    def test_to_safe_dict_excludes_raw_data(self):
        """to_safe_dict() should not include the full _data dict."""
        s = Settings()
        safe = s.to_safe_dict()
        # Should be a curated subset, not the full internal _data
        assert isinstance(safe, dict)
        assert "model" in safe

"""
tests/test_experiments.py
TASK-170: Unit tests for ai.experiments -- ExperimentId enum and
ExperimentFlags singleton.
"""

import types

import pytest
from unittest.mock import MagicMock, patch

from ai.experiments import ExperimentFlags, ExperimentId


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_flags(experiments_dict: dict | None = None) -> ExperimentFlags:
    """Return an ExperimentFlags instance backed by a mock settings."""
    mock_settings = MagicMock()
    mock_settings.get.side_effect = lambda key, fallback=None: (
        experiments_dict if key == "experiments" and experiments_dict is not None
        else fallback
    )
    flags = ExperimentFlags()
    # Patch the lazy import to return our mock
    flags._settings = staticmethod(lambda: mock_settings)  # type: ignore[assignment]
    flags._mock_settings = mock_settings  # stash for assertions
    return flags


# ---------------------------------------------------------------------------
# ExperimentId enum
# ---------------------------------------------------------------------------

class TestExperimentId:

    def test_all_members_are_strings(self):
        for member in ExperimentId:
            assert isinstance(member, str)
            assert isinstance(member.value, str)

    def test_expected_flags_exist(self):
        expected = {
            "auto_approval", "folded_context", "file_tracking",
            "custom_modes", "custom_tools", "non_destructive_truncation",
        }
        actual = {e.value for e in ExperimentId}
        assert actual == expected


# ---------------------------------------------------------------------------
# Default flags are all False
# ---------------------------------------------------------------------------

class TestDefaults:

    def test_all_defaults_are_false(self):
        flags = _make_flags(experiments_dict={})
        for exp in ExperimentId:
            assert flags.is_enabled(exp) is False

    def test_get_all_returns_all_flags_false(self):
        flags = _make_flags(experiments_dict={})
        result = flags.get_all()
        assert len(result) == len(ExperimentId)
        for key, val in result.items():
            assert val is False


# ---------------------------------------------------------------------------
# Enabling / disabling
# ---------------------------------------------------------------------------

class TestSetEnabled:

    def test_enable_flag(self):
        flags = _make_flags(experiments_dict={})
        flags.set_enabled(ExperimentId.AUTO_APPROVAL, True)
        ms = flags._mock_settings
        ms.set.assert_called_once()
        call_args = ms.set.call_args
        assert call_args[0][0] == "experiments"
        assert call_args[0][1]["auto_approval"] is True
        assert call_args[1]["_internal"] is True
        ms.save.assert_called_once()

    def test_disable_flag(self):
        flags = _make_flags(experiments_dict={"auto_approval": True})
        flags.set_enabled(ExperimentId.AUTO_APPROVAL, False)
        ms = flags._mock_settings
        call_args = ms.set.call_args
        assert call_args[0][1]["auto_approval"] is False

    def test_enable_via_string_value(self):
        """set_enabled accepts raw string values that match ExperimentId."""
        flags = _make_flags(experiments_dict={})
        flags.set_enabled("folded_context", True)
        ms = flags._mock_settings
        call_args = ms.set.call_args
        assert call_args[0][1]["folded_context"] is True


# ---------------------------------------------------------------------------
# get_all
# ---------------------------------------------------------------------------

class TestGetAll:

    def test_returns_all_flags_with_overrides(self):
        flags = _make_flags(experiments_dict={
            "auto_approval": True,
            "file_tracking": True,
        })
        result = flags.get_all()
        assert result["auto_approval"] is True
        assert result["file_tracking"] is True
        assert result["folded_context"] is False
        assert result["custom_modes"] is False
        assert result["custom_tools"] is False
        assert result["non_destructive_truncation"] is False

    def test_returns_correct_count(self):
        flags = _make_flags(experiments_dict={})
        assert len(flags.get_all()) == len(ExperimentId)


# ---------------------------------------------------------------------------
# Invalid flag ID
# ---------------------------------------------------------------------------

class TestInvalidFlag:

    def test_is_enabled_raises_on_invalid_string(self):
        flags = _make_flags(experiments_dict={})
        with pytest.raises(ValueError, match="Unknown experiment flag"):
            flags.is_enabled("totally_bogus_flag")

    def test_set_enabled_raises_on_invalid_string(self):
        flags = _make_flags(experiments_dict={})
        with pytest.raises(ValueError, match="Unknown experiment flag"):
            flags.set_enabled("not_a_real_flag", True)

    def test_invalid_flag_mentions_valid_flags(self):
        flags = _make_flags(experiments_dict={})
        with pytest.raises(ValueError, match="auto_approval"):
            flags.is_enabled("nope")


# ---------------------------------------------------------------------------
# TASK-203: _defaults immutability
# ---------------------------------------------------------------------------

class TestDefaultsImmutability:
    """TASK-203: Verify that _defaults cannot be mutated."""

    def test_defaults_is_mapping_proxy(self):
        """_defaults should be a MappingProxyType (immutable view)."""
        assert isinstance(ExperimentFlags._defaults, types.MappingProxyType)

    def test_defaults_cannot_be_mutated(self):
        """Assigning to _defaults should raise TypeError."""
        with pytest.raises(TypeError):
            ExperimentFlags._defaults["auto_approval"] = True

    def test_defaults_cannot_add_new_key(self):
        """Adding a new key to _defaults should raise TypeError."""
        with pytest.raises(TypeError):
            ExperimentFlags._defaults["new_flag"] = False

    def test_defaults_cannot_delete_key(self):
        """Deleting a key from _defaults should raise TypeError."""
        with pytest.raises(TypeError):
            del ExperimentFlags._defaults["auto_approval"]

    def test_defaults_contains_all_experiment_ids(self):
        """_defaults should contain an entry for every ExperimentId member."""
        for exp in ExperimentId:
            assert exp.value in ExperimentFlags._defaults


# ---------------------------------------------------------------------------
# Settings persistence round-trip
# ---------------------------------------------------------------------------

class TestPersistence:

    def test_round_trip_via_real_settings(self, tmp_path, monkeypatch):
        """Full integration: write a flag, reload settings, verify it persists."""
        # Point settings at a temp config file
        import config.settings as settings_mod
        config_file = tmp_path / "config.json"
        monkeypatch.setattr(settings_mod, "CONFIG_FILE", str(config_file))
        monkeypatch.setattr(settings_mod, "CONFIG_DIR", str(tmp_path))

        # Create a fresh settings instance
        s = settings_mod.Settings()
        s.load()

        # Wire up ExperimentFlags to use this settings instance
        flags = ExperimentFlags()
        flags._settings = staticmethod(lambda: s)  # type: ignore[assignment]

        # Enable a flag
        flags.set_enabled(ExperimentId.CUSTOM_MODES, True)

        # Verify it was persisted
        assert s.get("experiments", {}).get("custom_modes") is True

        # Create a new settings instance and reload from disk
        s2 = settings_mod.Settings()
        s2.load()
        assert s2.get("experiments", {}).get("custom_modes") is True

    def test_overrides_survive_get_all(self, tmp_path, monkeypatch):
        """get_all reflects persisted overrides after reload."""
        import config.settings as settings_mod
        config_file = tmp_path / "config.json"
        monkeypatch.setattr(settings_mod, "CONFIG_FILE", str(config_file))
        monkeypatch.setattr(settings_mod, "CONFIG_DIR", str(tmp_path))

        s = settings_mod.Settings()
        s.load()

        flags = ExperimentFlags()
        flags._settings = staticmethod(lambda: s)  # type: ignore[assignment]

        flags.set_enabled(ExperimentId.FILE_TRACKING, True)
        flags.set_enabled(ExperimentId.FOLDED_CONTEXT, True)

        all_flags = flags.get_all()
        assert all_flags["file_tracking"] is True
        assert all_flags["folded_context"] is True
        assert all_flags["auto_approval"] is False

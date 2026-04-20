"""
tests/test_modes.py
Tests for the CAD mode system.
"""

import json
import os
from unittest import mock

import pytest

from ai.modes import (
    CadMode,
    ModeManager,
    DEFAULT_MODES,
    BUILTIN_MODE_SLUGS,
    load_custom_modes,
    save_custom_modes,
)
from mcp.tool_groups import TOOL_GROUPS, get_tools_for_groups


class TestCadMode:
    """Tests for the CadMode class."""

    def test_construction(self):
        """CadMode stores all fields correctly."""
        mode = CadMode(
            slug="test",
            name="Test Mode",
            role_definition="You are a test agent.",
            tool_groups=["document", "query"],
            custom_instructions="Be precise.",
        )
        assert mode.slug == "test"
        assert mode.name == "Test Mode"
        assert mode.role_definition == "You are a test agent."
        assert mode.tool_groups == ["document", "query"]
        assert mode.custom_instructions == "Be precise."

    def test_get_allowed_tools(self):
        """get_allowed_tools returns the union of tools from the specified groups."""
        mode = CadMode(
            slug="t", name="T", role_definition="", tool_groups=["vision", "scripting"],
        )
        tools = mode.get_allowed_tools()
        assert "take_screenshot" in tools
        assert "execute_script" in tools
        assert "extrude" not in tools

    def test_to_dict(self):
        """to_dict produces a complete serialisation."""
        mode = CadMode(
            slug="s", name="S", role_definition="R",
            tool_groups=["vision"],
            custom_instructions="C",
        )
        d = mode.to_dict()
        assert d["slug"] == "s"
        assert d["name"] == "S"
        assert d["role_definition"] == "R"
        assert d["tool_groups"] == ["vision"]
        assert d["custom_instructions"] == "C"
        assert d["tool_count"] == 1  # only take_screenshot


class TestDefaultModes:
    """Tests for the predefined DEFAULT_MODES."""

    def test_full_mode_has_all_groups(self):
        """The 'full' mode includes every tool group.

        TASK-126: tool_groups is None (sentinel for 'all groups'),
        resolved dynamically via get_allowed_tools() and to_dict().
        """
        full = DEFAULT_MODES["full"]
        # tool_groups is None sentinel meaning "all available groups"
        assert full.tool_groups is None
        # But get_allowed_tools() should resolve to all tools
        assert full.get_allowed_tools() == get_tools_for_groups(list(TOOL_GROUPS.keys()))
        # to_dict() should expose the effective group list
        assert set(full.to_dict()["tool_groups"]) == set(TOOL_GROUPS.keys())

    def test_sketch_mode_groups(self):
        """The 'sketch' mode includes the expected groups."""
        sketch = DEFAULT_MODES["sketch"]
        assert "sketch" in sketch.tool_groups
        assert "document" in sketch.tool_groups
        assert "query" in sketch.tool_groups
        assert "primitives" not in sketch.tool_groups

    def test_all_default_modes_have_slugs(self):
        """Every default mode has a slug matching its key."""
        for key, mode in DEFAULT_MODES.items():
            assert mode.slug == key

    def test_expected_mode_slugs(self):
        """All expected mode slugs are present."""
        expected = {
            "full", "sketch", "modeling", "assembly",
            "analysis", "export", "scripting", "orchestrator",
        }
        assert set(DEFAULT_MODES.keys()) == expected


class TestModeManager:
    """Tests for the ModeManager class."""

    def test_default_mode_is_full(self):
        """The manager starts in 'full' mode."""
        mgr = ModeManager()
        assert mgr.active_slug == "full"
        assert mgr.active_mode.slug == "full"

    def test_switch_mode(self):
        """Switching to a valid mode updates the active mode."""
        mgr = ModeManager()
        mode = mgr.switch_mode("sketch")
        assert mode.slug == "sketch"
        assert mgr.active_slug == "sketch"
        assert mgr.active_mode.slug == "sketch"

    def test_switch_to_invalid_mode_raises(self):
        """Switching to an unknown mode raises ValueError."""
        mgr = ModeManager()
        with pytest.raises(ValueError, match="Unknown mode"):
            mgr.switch_mode("nonexistent")

    def test_switch_mode_changes_allowed_tools(self):
        """Switching modes changes the set of allowed tools."""
        mgr = ModeManager()
        full_tools = mgr.get_allowed_tools()
        mgr.switch_mode("analysis")
        analysis_tools = mgr.get_allowed_tools()
        # Analysis mode should have fewer tools than full
        assert len(analysis_tools) < len(full_tools)
        # Analysis mode should not have extrude
        assert "extrude" not in analysis_tools
        # But should have query tools
        assert "get_body_properties" in analysis_tools

    def test_get_mode(self):
        """get_mode returns the correct mode object or None."""
        mgr = ModeManager()
        assert mgr.get_mode("sketch").slug == "sketch"
        assert mgr.get_mode("nonexistent") is None

    def test_list_modes(self):
        """list_modes returns a list of dicts for all modes."""
        mgr = ModeManager()
        modes = mgr.list_modes()
        assert isinstance(modes, list)
        assert len(modes) == len(DEFAULT_MODES)
        slugs = {m["slug"] for m in modes}
        assert "full" in slugs
        assert "sketch" in slugs

    def test_list_modes_includes_tool_count(self):
        """Each mode dict in list_modes includes a tool_count."""
        mgr = ModeManager()
        for m in mgr.list_modes():
            assert "tool_count" in m
            assert isinstance(m["tool_count"], int)
            assert m["tool_count"] > 0

    def test_add_custom_mode(self, tmp_path):
        """Custom modes can be added and used."""
        # Patch CUSTOM_MODES_PATH so we don't write to real config
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text("[]", encoding="utf-8")

        with mock.patch("ai.modes.CUSTOM_MODES_PATH", str(custom_file)):
            mgr = ModeManager()
            custom = CadMode(
                slug="custom",
                name="Custom Mode",
                role_definition="Custom.",
                tool_groups=["vision"],
            )
            mgr.add_custom_mode(custom)
            mode = mgr.switch_mode("custom")
            assert mode.slug == "custom"
            assert mgr.get_allowed_tools() == {"take_screenshot"}

    def test_get_mode_prompt_additions_full(self):
        """Full mode returns empty prompt additions (no role_definition override)."""
        mgr = ModeManager()
        assert mgr.active_slug == "full"
        additions = mgr.get_mode_prompt_additions()
        # Full mode has no custom instructions and no role override
        assert additions == ""

    def test_get_mode_prompt_additions_sketch(self):
        """Non-full modes return prompt additions with role and instructions."""
        mgr = ModeManager()
        mgr.switch_mode("sketch")
        additions = mgr.get_mode_prompt_additions()
        assert "Current Mode: Sketch Mode" in additions
        assert "2D sketch specialist" in additions
        assert "create_sketch" in additions

    def test_modes_are_independent(self):
        """Two ModeManager instances do not share state."""
        mgr1 = ModeManager()
        mgr2 = ModeManager()
        mgr1.switch_mode("sketch")
        assert mgr2.active_slug == "full"

    def test_get_all_modes(self):
        """get_all_modes returns a dict of all modes."""
        mgr = ModeManager()
        all_modes = mgr.get_all_modes()
        assert isinstance(all_modes, dict)
        assert "full" in all_modes
        assert "sketch" in all_modes


# ---------------------------------------------------------------------------
# TASK-190: Prevent custom modes from shadowing built-in modes
# ---------------------------------------------------------------------------

class TestShadowProtection:
    """TASK-190: Verify that add_custom_mode() rejects built-in slugs."""

    def test_add_custom_mode_with_builtin_slug_raises(self, tmp_path):
        """Attempting to add a custom mode with a built-in slug raises ValueError."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text("[]", encoding="utf-8")

        with mock.patch("ai.modes.CUSTOM_MODES_PATH", str(custom_file)):
            mgr = ModeManager()
            for slug in BUILTIN_MODE_SLUGS:
                shadow = CadMode(
                    slug=slug,
                    name=f"Shadow {slug}",
                    role_definition="I shadow a built-in mode.",
                    tool_groups=["vision"],
                )
                with pytest.raises(ValueError, match="Cannot shadow built-in mode"):
                    mgr.add_custom_mode(shadow)

    def test_add_custom_mode_with_unique_slug_succeeds(self, tmp_path):
        """A custom mode with a non-built-in slug should be accepted."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text("[]", encoding="utf-8")

        with mock.patch("ai.modes.CUSTOM_MODES_PATH", str(custom_file)):
            mgr = ModeManager()
            unique = CadMode(
                slug="my-unique-mode",
                name="Unique Mode",
                role_definition="Unique.",
                tool_groups=["query"],
            )
            mgr.add_custom_mode(unique)  # Should not raise
            assert mgr.get_mode("my-unique-mode") is not None

    def test_builtin_modes_unchanged_after_shadow_attempt(self, tmp_path):
        """After a failed shadow attempt, the built-in mode is unchanged."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text("[]", encoding="utf-8")

        with mock.patch("ai.modes.CUSTOM_MODES_PATH", str(custom_file)):
            mgr = ModeManager()
            original_sketch = mgr.get_mode("sketch")
            shadow = CadMode(
                slug="sketch",
                name="Evil Sketch",
                role_definition="Shadow.",
                tool_groups=["vision"],
            )
            with pytest.raises(ValueError):
                mgr.add_custom_mode(shadow)
            # Original sketch mode should be untouched
            assert mgr.get_mode("sketch") is original_sketch
            assert mgr.get_mode("sketch").name == "Sketch Mode"


class TestOrchestratorMode:
    """Tests for the orchestrator mode definition and behaviour."""

    def test_orchestrator_in_default_modes(self):
        """The orchestrator mode exists in DEFAULT_MODES."""
        assert "orchestrator" in DEFAULT_MODES

    def test_orchestrator_slug_and_name(self):
        """Orchestrator has the correct slug and display name."""
        mode = DEFAULT_MODES["orchestrator"]
        assert mode.slug == "orchestrator"
        assert mode.name == "Orchestrator"

    def test_orchestrator_tool_groups_read_only(self):
        """Orchestrator is limited to read-only tool groups."""
        mode = DEFAULT_MODES["orchestrator"]
        assert mode.tool_groups == ["query", "vision"]

    def test_orchestrator_role_definition_key_phrases(self):
        """Role definition mentions coordination and decomposition."""
        role = DEFAULT_MODES["orchestrator"].role_definition
        assert "coordinator" in role.lower()
        assert "decompose" in role.lower()
        assert "delegate" in role.lower()

    def test_orchestrator_custom_instructions_protocol(self):
        """Custom instructions contain the orchestration protocol."""
        instructions = DEFAULT_MODES["orchestrator"].custom_instructions
        assert "ORCHESTRATION PROTOCOL" in instructions

    def test_manager_switch_to_orchestrator(self):
        """ModeManager can switch to orchestrator mode."""
        mgr = ModeManager()
        mode = mgr.switch_mode("orchestrator")
        assert mode.slug == "orchestrator"
        assert mgr.active_slug == "orchestrator"

    def test_orchestrator_allowed_tools(self):
        """Orchestrator tools are the union of query and vision groups only."""
        mgr = ModeManager()
        mgr.switch_mode("orchestrator")
        tools = mgr.get_allowed_tools()
        expected = set(TOOL_GROUPS["query"]) | set(TOOL_GROUPS["vision"])
        assert tools == expected
        # Must not include any modification tools
        assert "extrude" not in tools
        assert "create_box" not in tools
        assert "undo" not in tools

    def test_orchestrator_prompt_additions(self):
        """Prompt additions include the orchestration protocol."""
        mgr = ModeManager()
        mgr.switch_mode("orchestrator")
        additions = mgr.get_mode_prompt_additions()
        assert "Current Mode: Orchestrator" in additions
        assert "ORCHESTRATION PROTOCOL" in additions
        assert "DECOMPOSE" in additions


# ---------------------------------------------------------------------------
# TASK-168: Custom mode loading / saving / management
# ---------------------------------------------------------------------------


class TestLoadCustomModes:
    """Tests for load_custom_modes."""

    def test_returns_empty_when_file_missing(self, tmp_path):
        """load_custom_modes returns empty list when file doesn't exist."""
        result = load_custom_modes(str(tmp_path / "nonexistent.json"))
        assert result == []

    def test_loads_valid_modes(self, tmp_path):
        """load_custom_modes loads valid mode entries."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text(json.dumps([
            {
                "slug": "test-mode",
                "name": "Test Mode",
                "role_definition": "You are a tester.",
                "tool_groups": ["query"],
                "custom_instructions": "Test carefully.",
            },
            {
                "slug": "other_mode",
                "name": "Other Mode",
                "role_definition": "Another role.",
                "tool_groups": ["vision"],
            },
        ]), encoding="utf-8")

        modes = load_custom_modes(str(custom_file))
        assert len(modes) == 2
        assert modes[0].slug == "test-mode"
        assert modes[0].name == "Test Mode"
        assert modes[0].custom_instructions == "Test carefully."
        assert modes[1].slug == "other_mode"

    def test_skips_invalid_entries(self, tmp_path):
        """load_custom_modes skips entries missing required fields."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text(json.dumps([
            {"slug": "valid", "name": "Valid Mode"},
            {"slug": "", "name": "No Slug"},
            {"slug": "no-name"},
            "not-a-dict",
            42,
        ]), encoding="utf-8")

        modes = load_custom_modes(str(custom_file))
        assert len(modes) == 1
        assert modes[0].slug == "valid"

    def test_validates_slug_format(self, tmp_path):
        """load_custom_modes rejects slugs with invalid characters."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text(json.dumps([
            {"slug": "valid-slug_123", "name": "Good"},
            {"slug": "bad slug!", "name": "Bad"},
            {"slug": "../traversal", "name": "Evil"},
        ]), encoding="utf-8")

        modes = load_custom_modes(str(custom_file))
        assert len(modes) == 1
        assert modes[0].slug == "valid-slug_123"

    def test_handles_invalid_json(self, tmp_path):
        """load_custom_modes handles corrupt JSON gracefully."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text("{not valid json", encoding="utf-8")

        modes = load_custom_modes(str(custom_file))
        assert modes == []

    def test_handles_non_array_json(self, tmp_path):
        """load_custom_modes handles non-array JSON root."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text('{"not": "an array"}', encoding="utf-8")

        modes = load_custom_modes(str(custom_file))
        assert modes == []


class TestSaveCustomModes:
    """Tests for save_custom_modes."""

    def test_saves_modes_to_file(self, tmp_path):
        """save_custom_modes writes modes as a JSON array."""
        custom_file = tmp_path / "custom_modes.json"
        modes = [
            CadMode(slug="my-mode", name="My Mode", role_definition="Role",
                     tool_groups=["query"], custom_instructions="Instructions"),
        ]
        save_custom_modes(modes, str(custom_file))

        with open(custom_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 1
        assert data[0]["slug"] == "my-mode"
        assert data[0]["name"] == "My Mode"
        assert data[0]["tool_groups"] == ["query"]

    def test_round_trip(self, tmp_path):
        """Modes saved by save_custom_modes can be loaded by load_custom_modes."""
        custom_file = tmp_path / "custom_modes.json"
        original = [
            CadMode(slug="rt-mode", name="Round Trip", role_definition="Test",
                     tool_groups=["vision"], custom_instructions=""),
        ]
        save_custom_modes(original, str(custom_file))
        loaded = load_custom_modes(str(custom_file))
        assert len(loaded) == 1
        assert loaded[0].slug == "rt-mode"
        assert loaded[0].name == "Round Trip"


class TestCustomModesIntegration:
    """Tests for custom mode integration with ModeManager."""

    def _make_custom_file(self, tmp_path, modes_data: list) -> str:
        """Helper to create a custom modes file and return its path."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text(json.dumps(modes_data), encoding="utf-8")
        return str(custom_file)

    def test_custom_modes_loaded_when_experiment_enabled(self, tmp_path):
        """Custom modes appear in get_all_modes when experiment flag is enabled."""
        custom_file = self._make_custom_file(tmp_path, [
            {"slug": "my-custom", "name": "My Custom", "role_definition": "Custom role"},
        ])

        with mock.patch("ai.modes.CUSTOM_MODES_PATH", custom_file):
            with mock.patch("ai.experiments.ExperimentFlags.is_enabled", return_value=True):
                mgr = ModeManager()

        assert "my-custom" in mgr.get_all_modes()
        assert mgr.get_mode("my-custom").name == "My Custom"

    def test_custom_modes_not_loaded_when_experiment_disabled(self, tmp_path):
        """Custom modes are NOT loaded when CUSTOM_MODES flag is disabled."""
        custom_file = self._make_custom_file(tmp_path, [
            {"slug": "hidden", "name": "Hidden Mode", "role_definition": ""},
        ])

        with mock.patch("ai.modes.CUSTOM_MODES_PATH", custom_file):
            with mock.patch("ai.experiments.ExperimentFlags.is_enabled", return_value=False):
                mgr = ModeManager()

        assert "hidden" not in mgr.get_all_modes()

    def test_custom_mode_overrides_builtin(self, tmp_path):
        """Custom mode with a built-in slug overrides the built-in mode at load time."""
        custom_file = self._make_custom_file(tmp_path, [
            {
                "slug": "sketch",
                "name": "Custom Sketch",
                "role_definition": "Custom sketch role.",
                "tool_groups": ["vision"],
            },
        ])

        with mock.patch("ai.modes.CUSTOM_MODES_PATH", custom_file):
            with mock.patch("ai.experiments.ExperimentFlags.is_enabled", return_value=True):
                mgr = ModeManager()

        assert mgr.get_mode("sketch").name == "Custom Sketch"

    def test_add_custom_mode_persists(self, tmp_path):
        """add_custom_mode persists the mode to the custom modes file."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text("[]", encoding="utf-8")

        with mock.patch("ai.modes.CUSTOM_MODES_PATH", str(custom_file)):
            mgr = ModeManager()
            new_mode = CadMode(
                slug="persisted",
                name="Persisted Mode",
                role_definition="I persist.",
                tool_groups=["query"],
            )
            mgr.add_custom_mode(new_mode)

        # Verify file was written
        with open(custom_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["slug"] == "persisted"

    def test_remove_custom_mode_persists(self, tmp_path):
        """remove_custom_mode removes the mode and persists the change."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text("[]", encoding="utf-8")

        with mock.patch("ai.modes.CUSTOM_MODES_PATH", str(custom_file)):
            mgr = ModeManager()
            mgr.add_custom_mode(CadMode(
                slug="removable", name="Removable", role_definition="",
                tool_groups=["query"],
            ))
            assert mgr.get_mode("removable") is not None

            result = mgr.remove_custom_mode("removable")
            assert result is True
            assert mgr.get_mode("removable") is None

        # Verify file no longer contains the mode
        with open(custom_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 0

    def test_remove_builtin_mode_fails(self):
        """remove_custom_mode refuses to remove built-in modes."""
        mgr = ModeManager()
        result = mgr.remove_custom_mode("full")
        assert result is False
        assert mgr.get_mode("full") is not None

    def test_remove_nonexistent_returns_false(self):
        """remove_custom_mode returns False for unknown slugs."""
        mgr = ModeManager()
        result = mgr.remove_custom_mode("does-not-exist")
        assert result is False

    def test_list_custom_modes_excludes_builtins(self, tmp_path):
        """list_custom_modes returns only non-built-in modes."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text("[]", encoding="utf-8")

        with mock.patch("ai.modes.CUSTOM_MODES_PATH", str(custom_file)):
            mgr = ModeManager()
            mgr.add_custom_mode(CadMode(
                slug="user-mode", name="User Mode", role_definition="",
                tool_groups=["query"],
            ))

        custom = mgr.list_custom_modes()
        slugs = [m.slug for m in custom]
        assert "user-mode" in slugs
        assert "full" not in slugs
        assert "sketch" not in slugs

    def test_remove_active_custom_mode_resets_to_full(self, tmp_path):
        """Removing the active custom mode resets to 'full'."""
        custom_file = tmp_path / "custom_modes.json"
        custom_file.write_text("[]", encoding="utf-8")

        with mock.patch("ai.modes.CUSTOM_MODES_PATH", str(custom_file)):
            mgr = ModeManager()
            mgr.add_custom_mode(CadMode(
                slug="temp", name="Temp", role_definition="",
                tool_groups=["query"],
            ))
            mgr.switch_mode("temp")
            assert mgr.active_slug == "temp"

            mgr.remove_custom_mode("temp")
            assert mgr.active_slug == "full"

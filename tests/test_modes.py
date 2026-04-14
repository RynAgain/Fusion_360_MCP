"""
tests/test_modes.py
Tests for the CAD mode system.
"""

import pytest

from ai.modes import CadMode, ModeManager, DEFAULT_MODES
from mcp.tool_groups import TOOL_GROUPS


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
        """The 'full' mode includes every tool group."""
        full = DEFAULT_MODES["full"]
        assert set(full.tool_groups) == set(TOOL_GROUPS.keys())

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

    def test_add_custom_mode(self):
        """Custom modes can be added and used."""
        mgr = ModeManager()
        custom = CadMode(
            slug="custom",
            name="Custom Mode",
            role_definition="Custom.",
            tool_groups=["vision"],
        )
        mgr.add_custom_mode("custom", custom)
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

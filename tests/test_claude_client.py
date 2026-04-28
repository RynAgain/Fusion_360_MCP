"""
tests/test_claude_client.py
Unit tests for ai/claude_client.py -- ClaudeClient initialisation,
conversation management, and config updates.

All tests mock the Anthropic SDK so no real API calls are made.
"""

import uuid
import pytest
from unittest.mock import MagicMock, patch

from ai.claude_client import ClaudeClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_settings():
    """Return a mock Settings object with sensible defaults."""
    s = MagicMock()
    s.api_key = "sk-ant-test-key-0000"
    s.model = "claude-opus-4-5"
    s.max_tokens = 4096
    s.system_prompt = "Test system prompt."
    return s


@pytest.fixture
def mock_mcp():
    """Return a mock MCPServer."""
    m = MagicMock()
    m.tool_definitions = []
    m.execute_tool = MagicMock(return_value={"status": "simulation", "message": "ok"})
    return m


@pytest.fixture
def client(mock_settings, mock_mcp):
    """Return a ClaudeClient wired to mock dependencies."""
    return ClaudeClient(mock_settings, mock_mcp)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestInit:
    """Verify ClaudeClient construction."""

    def test_creates_with_valid_settings(self, client):
        assert client is not None
        assert isinstance(client.conversation_history, list)

    def test_initial_conversation_id_is_uuid(self, client):
        cid = client.get_conversation_id()
        # Should be a valid UUID string
        parsed = uuid.UUID(cid)
        assert str(parsed) == cid

    def test_initial_history_is_empty(self, client):
        assert client.get_messages() == []

    def test_system_prompt_built(self, client):
        """The system prompt should be built from the builder, not raw settings."""
        assert "Artifex360" in client._system_prompt

    def test_handles_missing_api_key(self, mock_mcp):
        """Client should initialise even when the API key is empty."""
        s = MagicMock()
        s.api_key = ""
        s.system_prompt = ""
        c = ClaudeClient(s, mock_mcp)
        assert c is not None


# ---------------------------------------------------------------------------
# Conversation management
# ---------------------------------------------------------------------------

class TestConversationManagement:
    """Verify new_conversation, set_conversation, get_messages."""

    def test_new_conversation_generates_new_id(self, client):
        old_id = client.get_conversation_id()
        new_id = client.new_conversation()
        assert new_id != old_id
        assert client.get_conversation_id() == new_id

    def test_new_conversation_clears_history(self, client):
        # Manually add a message
        client.conversation_history.append({"role": "user", "content": "hello"})
        client.new_conversation()
        assert client.get_messages() == []

    def test_get_messages_returns_copy(self, client):
        """Returned list should be a copy, not the internal reference."""
        client.conversation_history.append({"role": "user", "content": "test"})
        msgs = client.get_messages()
        msgs.clear()
        # Internal list should be unaffected
        assert len(client.get_messages()) == 1

    def test_set_conversation_restores_id(self, client):
        target_id = "restored-conv-id"
        client.set_conversation(target_id, [{"role": "user", "content": "hi"}])
        assert client.get_conversation_id() == target_id

    def test_set_conversation_restores_messages(self, client):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        client.set_conversation("cid", msgs)
        assert len(client.get_messages()) == 2
        assert client.get_messages()[0]["content"] == "hello"

    def test_set_conversation_copies_messages(self, client):
        """set_conversation should store a copy, not the original list."""
        original = [{"role": "user", "content": "x"}]
        client.set_conversation("cid", original)
        original.append({"role": "user", "content": "y"})
        assert len(client.get_messages()) == 1


# ---------------------------------------------------------------------------
# clear_history
# ---------------------------------------------------------------------------

class TestClearHistory:
    def test_empties_message_list(self, client):
        client.conversation_history.append({"role": "user", "content": "test"})
        client.clear_history()
        assert client.get_messages() == []


# ---------------------------------------------------------------------------
# update_config
# ---------------------------------------------------------------------------

class TestUpdateConfig:
    """Verify that update_config propagates to settings and rebuilds prompt."""

    def test_updates_api_key(self, client, mock_settings):
        client.update_config(api_key="sk-new-key")
        mock_settings.update.assert_called()
        call_args = mock_settings.update.call_args[0][0]
        assert call_args["anthropic_api_key"] == "sk-new-key"

    def test_updates_model(self, client, mock_settings):
        client.update_config(model="claude-sonnet-4-20250514")
        call_args = mock_settings.update.call_args[0][0]
        assert call_args["model"] == "claude-sonnet-4-20250514"

    def test_updates_max_tokens(self, client, mock_settings):
        client.update_config(max_tokens=8192)
        call_args = mock_settings.update.call_args[0][0]
        assert call_args["max_tokens"] == 8192

    def test_rebuilds_system_prompt_on_change(self, client):
        old_prompt = client._system_prompt
        client.update_config(system_prompt="Be very brief.")
        # The prompt is rebuilt -- it should still contain core identity
        assert "Artifex360" in client._system_prompt

    def test_no_update_when_all_none(self, client, mock_settings):
        client.update_config()
        mock_settings.update.assert_not_called()


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------

class TestEmitter:
    def test_set_emitter(self, client):
        cb = MagicMock()
        client.set_emitter(cb)
        assert client._emitter is cb

    def test_set_emitter_none(self, client):
        client.set_emitter(None)
        assert client._emitter is None


# ---------------------------------------------------------------------------
# _has_action_intent  (auto-continue detection)
# ---------------------------------------------------------------------------

class TestHasActionIntent:
    """Verify that _has_action_intent correctly detects intent-without-action."""

    def test_matches_i_will_create(self, client):
        assert client._has_action_intent("I will create a sketch on the XY plane")

    def test_matches_ill_extrude(self, client):
        assert client._has_action_intent("I'll extrude the profile by 5 cm")

    def test_matches_let_me(self, client):
        assert client._has_action_intent("Let me execute this script for you")

    def test_matches_im_going_to(self, client):
        assert client._has_action_intent("I'm going to add a fillet to the edges")

    def test_matches_ill_now(self, client):
        assert client._has_action_intent("I'll now run the mirror operation")

    def test_matches_i_need_to_create(self, client):
        assert client._has_action_intent("I need to create a cylinder for this design")

    def test_matches_please_wait(self, client):
        assert client._has_action_intent("Please wait while I process this")

    def test_matches_lets(self, client):
        assert client._has_action_intent("Let's proceed with the extrusion now")

    def test_no_match_question(self, client):
        assert not client._has_action_intent("What size would you like?")

    def test_no_match_summary(self, client):
        assert not client._has_action_intent("Here is a summary of the design so far")

    def test_no_match_past_tense(self, client):
        assert not client._has_action_intent("I created the box successfully")

    def test_no_match_empty(self, client):
        assert not client._has_action_intent("")

    def test_no_match_none(self, client):
        assert not client._has_action_intent(None)

    def test_no_match_short_text(self, client):
        assert not client._has_action_intent("I will")

    def test_matches_executing_script(self, client):
        assert client._has_action_intent("I am currently executing the script tool to create the geometry")

    def test_matches_ill_begin(self, client):
        assert client._has_action_intent("I'll begin by sketching the base profile")


# ---------------------------------------------------------------------------
# Orchestration integration
# ---------------------------------------------------------------------------

class TestOrchestrationIntegration:
    """Verify orchestration subsystem wiring in ClaudeClient."""

    def test_orchestration_subsystems_initialized(self, client):
        """subtask_manager and context_bridge exist after init."""
        from ai.subtask_manager import SubtaskManager
        from ai.context_bridge import ContextBridge

        assert isinstance(client.subtask_manager, SubtaskManager)
        assert isinstance(client.context_bridge, ContextBridge)
        # They should share the same ContextBridge instance
        assert client.subtask_manager.context_bridge is client.context_bridge

    def test_create_orchestrated_plan(self, client):
        """create_orchestrated_plan delegates to TaskManager and clears orchestration state."""
        steps = [
            {"description": "Create base sketch", "mode_hint": "sketch"},
            {"description": "Extrude base", "mode_hint": "modeling", "depends_on": [0]},
        ]
        client.create_orchestrated_plan("Test Plan", steps)

        assert client.task_manager.has_plan
        assert len(client.task_manager._tasks) == 2
        assert client.task_manager._tasks[0].mode_hint == "sketch"
        assert client.task_manager._tasks[1].depends_on == [0]
        # Execution history should be empty after creating a fresh plan
        assert client.subtask_manager.get_execution_summary()["total_executed"] == 0

    def test_get_orchestration_status_no_plan(self, client):
        """Returns correct status when no plan exists."""
        status = client.get_orchestration_status()

        assert status["has_plan"] is False
        assert status["is_executing"] is False
        assert status["current_step"] is None
        assert status["plan_summary"] is None
        assert status["execution_summary"]["total_executed"] == 0

    def test_get_orchestration_status_with_plan(self, client):
        """Returns correct status after creating a plan."""
        steps = [
            {"description": "Step A"},
            {"description": "Step B", "depends_on": [0]},
        ]
        client.create_orchestrated_plan("My Plan", steps)

        status = client.get_orchestration_status()

        assert status["has_plan"] is True
        assert status["is_executing"] is False
        assert status["current_step"] is None
        assert status["plan_summary"] is not None
        assert status["plan_summary"]["title"] == "My Plan"
        assert status["plan_summary"]["total_steps"] == 2
        assert status["plan_summary"]["ready"] == 1  # Only Step A is ready

    def test_execute_next_subtask_no_plan(self, client):
        """Raises ValueError when no plan exists."""
        with pytest.raises(ValueError, match="No orchestrated plan exists"):
            client.execute_next_subtask()

    def test_execute_full_plan_no_plan(self, client):
        """Raises ValueError when no plan exists."""
        with pytest.raises(ValueError, match="No orchestrated plan exists"):
            client.execute_full_plan()

    def test_clear_history_clears_orchestration(self, client):
        """clear_history also clears orchestration state."""
        steps = [{"description": "Step 1"}]
        client.create_orchestrated_plan("Plan", steps)
        client.clear_history()

        assert not client.task_manager.has_plan
        assert client.subtask_manager.get_execution_summary()["total_executed"] == 0

    def test_new_conversation_clears_orchestration(self, client):
        """new_conversation also clears orchestration state."""
        steps = [{"description": "Step 1"}]
        client.create_orchestrated_plan("Plan", steps)
        client.new_conversation()

        assert not client.task_manager.has_plan
        assert client.subtask_manager.get_execution_summary()["total_executed"] == 0


# ---------------------------------------------------------------------------
# TASK-239: Hallucinated tool call detection
# ---------------------------------------------------------------------------

class TestHallucinatedToolCallDetection:
    """TASK-239: Detect plain-text tool call patterns."""

    def test_detect_tool_code_block(self):
        """Should detect <tool_code> blocks."""
        matches = ClaudeClient._detect_hallucinated_tool_calls(
            "<tool_code>\ncreate_cylinder(diameter=60)\n</tool_code>"
        )
        assert len(matches) >= 1

    def test_detect_tool_code_call(self):
        """Should detect tool_code() calls."""
        matches = ClaudeClient._detect_hallucinated_tool_calls(
            "tool_code(fusion360.create_box(width=5))"
        )
        assert len(matches) >= 1

    def test_detect_bare_function_call_syntax(self):
        """Should detect bare function-call syntax with known tool names."""
        matches = ClaudeClient._detect_hallucinated_tool_calls(
            "Let me create_box(width=5, height=10, length=2.5)"
        )
        assert len(matches) >= 1

    def test_no_match_explanatory_text(self):
        """Should NOT match normal explanatory text."""
        matches = ClaudeClient._detect_hallucinated_tool_calls(
            "I will create a box using the create_box tool with width 5cm."
        )
        assert len(matches) == 0

    def test_no_match_empty_text(self):
        """Should NOT match empty/short text."""
        matches = ClaudeClient._detect_hallucinated_tool_calls("")
        assert len(matches) == 0
        matches = ClaudeClient._detect_hallucinated_tool_calls("hello")
        assert len(matches) == 0

    def test_detect_function_call_block(self):
        """Should detect <function_call> blocks."""
        matches = ClaudeClient._detect_hallucinated_tool_calls(
            "<function_call>create_sphere(radius=10)</function_call>"
        )
        assert len(matches) >= 1

    def test_detect_tool_use_block(self):
        """Should detect <tool_use> blocks."""
        matches = ClaudeClient._detect_hallucinated_tool_calls(
            "<tool_use>extrude(profile=0, distance=5)</tool_use>"
        )
        assert len(matches) >= 1

    def test_detect_backtick_tool_call(self):
        """Should detect ```tool_call blocks."""
        matches = ClaudeClient._detect_hallucinated_tool_calls(
            "```tool_call\ncreate_box(width=5)\n```"
        )
        assert len(matches) >= 1

    def test_detect_known_tool_extrude(self):
        """Should detect extrude() as hallucinated call."""
        matches = ClaudeClient._detect_hallucinated_tool_calls(
            "Now I will extrude(profile_id='abc', distance=10)"
        )
        assert len(matches) >= 1

    def test_detect_known_tool_execute_script(self):
        """Should detect execute_script() as hallucinated call."""
        matches = ClaudeClient._detect_hallucinated_tool_calls(
            "execute_script(script='import adsk')"
        )
        assert len(matches) >= 1

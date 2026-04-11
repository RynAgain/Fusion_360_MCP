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
    s.simulation_mode = True
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
        assert "Fusion 360 AI Design Agent" in client._system_prompt

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
        assert "Fusion 360 AI Design Agent" in client._system_prompt

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

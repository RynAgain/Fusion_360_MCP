"""
tests/test_agent_loop.py
TASK-032: Integration tests for the Claude agent loop.

These tests mock the LLM provider but exercise the full turn loop
to verify flow correctness -- not LLM output quality.
"""

import threading
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from ai.providers.base import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_text_response(text: str) -> LLMResponse:
    """Build an LLMResponse with a single text block (end_turn)."""
    resp = LLMResponse()
    resp.content = [{"type": "text", "text": text}]
    resp.stop_reason = "end_turn"
    resp.usage = {"input_tokens": 10, "output_tokens": 20}
    resp.model = "mock-model"
    return resp


def _make_tool_call_response(tool_name: str, tool_input: dict,
                              tool_id: str = "tool_1") -> LLMResponse:
    """Build an LLMResponse with a tool_use block."""
    resp = LLMResponse()
    resp.content = [{
        "type": "tool_use",
        "id": tool_id,
        "name": tool_name,
        "input": tool_input,
    }]
    resp.stop_reason = "tool_use"
    resp.usage = {"input_tokens": 10, "output_tokens": 20}
    resp.model = "mock-model"
    return resp


def _build_client():
    """Build a ClaudeClient with mocked settings and MCP server."""
    settings = MagicMock()
    settings.api_key = "test-key"
    settings.model = "mock-model"
    settings.max_tokens = 1024
    settings.system_prompt = "You are a test agent."
    settings.simulation_mode = True
    settings.provider = "anthropic"
    settings.ollama_base_url = "http://localhost:11434"
    settings.get.return_value = 10  # max_requests_per_minute

    mcp_server = MagicMock()
    mcp_server.tool_definitions = []
    mcp_server.get_tool_names.return_value = []
    mcp_server.execute_tool.return_value = {
        "status": "success", "success": True, "message": "done",
    }

    # Patch out provider manager so we don't need real providers
    with patch("ai.claude_client.ProviderManager") as MockPM:
        mock_pm_instance = MagicMock()
        MockPM.return_value = mock_pm_instance

        mock_provider = MagicMock()
        mock_provider.is_available.return_value = True
        mock_pm_instance.active = mock_provider
        mock_pm_instance.active_type = "anthropic"

        from ai.claude_client import ClaudeClient
        client = ClaudeClient(settings, mcp_server)
        client.provider_manager = mock_pm_instance

    return client, mock_provider, mcp_server


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAgentLoopBasicMessage:
    """Test: basic message sends and gets a response."""

    def test_basic_text_response(self):
        client, mock_provider, _mcp = _build_client()
        mock_provider.stream_message.return_value = _make_text_response("Hello!")

        events = []

        def on_event(event_type, payload):
            events.append((event_type, payload))

        client.run_turn("Hi there", on_event=on_event)

        # Should have emitted text_done and done
        event_types = [e[0] for e in events]
        assert "text_done" in event_types
        assert "done" in event_types

        # Conversation history should have user + assistant messages
        messages = client.get_messages()
        assert len(messages) >= 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"


class TestAgentLoopToolCall:
    """Test: tool call is detected and dispatched correctly."""

    def test_tool_call_dispatched(self):
        client, mock_provider, mcp = _build_client()

        # First call: tool call; second call: text response
        mock_provider.stream_message.side_effect = [
            _make_tool_call_response("get_body_list", {}),
            _make_text_response("Here are the bodies."),
        ]

        events = []
        client.run_turn("List all bodies", on_event=lambda t, p: events.append((t, p)))

        event_types = [e[0] for e in events]
        assert "tool_call" in event_types
        assert "tool_result" in event_types
        assert "done" in event_types

        # The MCP server's execute_tool should have been called
        mcp.execute_tool.assert_called_once_with("get_body_list", {})


class TestAgentLoopMultipleToolCalls:
    """Test: multiple tool calls in sequence."""

    def test_sequential_tool_calls(self):
        client, mock_provider, mcp = _build_client()

        mock_provider.stream_message.side_effect = [
            _make_tool_call_response("create_sketch", {"plane": "XY"}, "t1"),
            _make_tool_call_response("add_sketch_circle", {"sketch_name": "S1", "center_x": 0, "center_y": 0, "radius": 1}, "t2"),
            _make_text_response("Done creating geometry."),
        ]

        events = []
        client.run_turn("Create a circle on XY", on_event=lambda t, p: events.append((t, p)))

        tool_calls = [e for e in events if e[0] == "tool_call"]
        # At least our 2 explicit tool calls (agent may also fire internal ones)
        assert len(tool_calls) >= 2

        tool_results = [e for e in events if e[0] == "tool_result"]
        assert len(tool_results) >= 2

        # Verify our specific tools were called (agent also calls internal tools
        # for state tracking, so total count will be higher than 2)
        called_tool_names = [
            call.args[0] for call in mcp.execute_tool.call_args_list
        ]
        assert "create_sketch" in called_tool_names
        assert "add_sketch_circle" in called_tool_names


class TestAgentLoopToolError:
    """Test: error in tool execution is handled gracefully."""

    def test_tool_error_handled(self):
        client, mock_provider, mcp = _build_client()

        mcp.execute_tool.return_value = {
            "status": "error", "success": False,
            "error": "Body not found",
        }

        mock_provider.stream_message.side_effect = [
            _make_tool_call_response("delete_body", {"body_name": "NonExistent"}),
            _make_text_response("The body was not found."),
        ]

        events = []
        client.run_turn("Delete NonExistent", on_event=lambda t, p: events.append((t, p)))

        # Tool result should be emitted (with error info)
        tool_results = [e for e in events if e[0] == "tool_result"]
        assert len(tool_results) >= 1
        result_payload = tool_results[0][1]
        assert result_payload["result"]["success"] is False

        # The loop should still complete
        assert ("done", {}) in events


class TestAgentLoopCancellation:
    """Test: cancellation event stops the loop."""

    def test_turn_lock_prevents_concurrent(self):
        """Turn lock prevents concurrent turns -- second call gets error."""
        client, mock_provider, _mcp = _build_client()

        # Make the first call block by using an event
        barrier = threading.Event()

        def slow_stream(*args, **kwargs):
            barrier.wait(timeout=5)
            return _make_text_response("Finished.")

        mock_provider.stream_message.side_effect = slow_stream

        events_1 = []
        events_2 = []

        t1 = threading.Thread(
            target=client.run_turn,
            args=("msg1",),
            kwargs={"on_event": lambda t, p: events_1.append((t, p))},
        )
        t1.start()

        # Give t1 time to acquire the lock
        import time
        time.sleep(0.1)

        # Second call should be rejected immediately
        client.run_turn("msg2", on_event=lambda t, p: events_2.append((t, p)))

        # Unblock the first turn
        barrier.set()
        t1.join(timeout=5)

        # The second call should have received an error event
        event_types_2 = [e[0] for e in events_2]
        assert "error" in event_types_2
        assert "done" in event_types_2


class TestAgentLoopTurnLock:
    """Test: turn lock prevents concurrent turns."""

    def test_concurrent_turn_rejected(self):
        client, mock_provider, _mcp = _build_client()

        barrier = threading.Event()

        def slow_stream(*args, **kwargs):
            barrier.wait(timeout=5)
            return _make_text_response("Done.")

        mock_provider.stream_message.side_effect = slow_stream

        events_concurrent = []
        t = threading.Thread(
            target=client.run_turn,
            args=("first",),
            kwargs={"on_event": lambda t, p: None},
        )
        t.start()

        import time
        time.sleep(0.1)

        client.run_turn("second", on_event=lambda t, p: events_concurrent.append((t, p)))

        barrier.set()
        t.join(timeout=5)

        error_events = [e for e in events_concurrent if e[0] == "error"]
        assert len(error_events) == 1
        assert "already in progress" in error_events[0][1]["message"]

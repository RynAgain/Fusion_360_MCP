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
    settings.max_tokens = 128000
    settings.system_prompt = "You are a test agent."
    settings.provider = "anthropic"
    settings.ollama_base_url = "http://localhost:11434"
    # Return sensible defaults per key; fall back to 10 for unknown keys
    # (e.g. max_requests_per_minute).
    _settings_map = {
        "agent_iteration_warning_threshold": 0.80,
        "web_research_max_consecutive_failures": 3,
    }
    settings.get.side_effect = lambda key, fallback=None: _settings_map.get(
        key, fallback if fallback is not None else 10,
    )

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
        # TASK-114: Use threading.Event instead of time.sleep for sync
        started = threading.Event()

        def slow_stream(*args, **kwargs):
            started.set()  # Signal that the first turn has started
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

        # TASK-114: Wait for the first turn to actually start (acquire lock)
        started.wait(timeout=5.0)

        # Second call should be rejected immediately
        client.run_turn("msg2", on_event=lambda t, p: events_2.append((t, p)))

        # Unblock the first turn
        barrier.set()
        t1.join(timeout=5)

        # The second call should have received an error event
        event_types_2 = [e[0] for e in events_2]
        assert "error" in event_types_2
        assert "done" in event_types_2


class TestAgentLoopMidTurnCancellation:
    """Test: mid-turn stopping via iteration limit or force-stop."""

    def test_cancel_stops_mid_turn(self):
        """Iteration limit should stop the loop between tool calls."""
        client, mock_provider, mcp = _build_client()

        # Make the provider always return a tool_use response so the loop
        # would run forever without the iteration guard.
        mock_provider.stream_message.side_effect = lambda **kwargs: (
            _make_tool_call_response(
                "get_body_list", {}, f"tool_{mock_provider.stream_message.call_count}"
            )
        )

        # Lower the max iterations to make the test fast
        original_max = client._MAX_AGENT_ITERATIONS
        client._MAX_AGENT_ITERATIONS = 3

        events = []
        try:
            client.run_turn(
                "Loop forever",
                on_event=lambda t, p: events.append((t, p)),
            )
        finally:
            client._MAX_AGENT_ITERATIONS = original_max

        # The loop should have completed (not hung)
        event_types = [e[0] for e in events]
        assert "done" in event_types

        # The provider should have been called approximately
        # _MAX_AGENT_ITERATIONS times (3), not unboundedly
        assert mock_provider.stream_message.call_count <= 5


class TestAgentLoopTurnLock:
    """Test: turn lock prevents concurrent turns."""

    def test_concurrent_turn_rejected(self):
        client, mock_provider, _mcp = _build_client()

        barrier = threading.Event()
        # TASK-114: Use threading.Event instead of time.sleep for sync
        started = threading.Event()

        def slow_stream(*args, **kwargs):
            started.set()  # Signal that the first turn has started
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

        # TASK-114: Wait for the first turn to actually start (acquire lock)
        started.wait(timeout=5.0)

        client.run_turn("second", on_event=lambda t, p: events_concurrent.append((t, p)))

        barrier.set()
        t.join(timeout=5)

        error_events = [e for e in events_concurrent if e[0] == "error"]
        assert len(error_events) == 1
        assert "already in progress" in error_events[0][1]["message"]


# ---------------------------------------------------------------------------
# TASK-223: Early warning at iteration threshold
# ---------------------------------------------------------------------------

class TestAgentLoopIterationWarning:
    """Test: early warning injected at configurable iteration threshold."""

    def test_warning_injected_at_80_percent(self):
        """At iteration 4 of 5 (80%), a warning message is injected."""
        client, mock_provider, mcp = _build_client()

        # We need exactly max_iterations calls that return tool_use,
        # then the loop breaks on the max+1 iteration.
        # Set max to 5 -- warning should fire at iteration 4 (80% of 5).
        client._MAX_AGENT_ITERATIONS = 5

        call_count = [0]

        def make_response(**kwargs):
            call_count[0] += 1
            return _make_tool_call_response(
                "get_body_list", {}, f"tool_{call_count[0]}"
            )

        mock_provider.stream_message.side_effect = make_response

        events = []
        client.run_turn(
            "Loop test",
            on_event=lambda t, p: events.append((t, p)),
        )

        # Collect all messages that were in the conversation
        messages = client.get_messages()

        # Find the warning message
        warning_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "[SYSTEM] Warning: You have used" in m["content"]
        ]
        assert len(warning_msgs) == 1, (
            f"Expected exactly 1 warning message, found {len(warning_msgs)}"
        )
        assert "remaining" in warning_msgs[0]["content"]

    def test_warning_not_injected_below_threshold(self):
        """When iterations stay below the threshold, no warning is injected."""
        client, mock_provider, mcp = _build_client()
        client._MAX_AGENT_ITERATIONS = 50

        # 2 tool calls then text -- well below 80% of 50 = 40
        mock_provider.stream_message.side_effect = [
            _make_tool_call_response("get_body_list", {}, "t1"),
            _make_tool_call_response("get_body_list", {}, "t2"),
            _make_text_response("Done."),
        ]

        events = []
        client.run_turn("Quick test", on_event=lambda t, p: events.append((t, p)))

        messages = client.get_messages()
        warning_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "[SYSTEM] Warning: You have used" in m["content"]
        ]
        assert len(warning_msgs) == 0

    def test_warning_threshold_configurable(self):
        """Custom threshold from settings changes when warning fires."""
        client, mock_provider, mcp = _build_client()
        client._MAX_AGENT_ITERATIONS = 10
        # Set threshold to 50% -- warning should fire at iteration 5
        client.settings.get.side_effect = lambda key, fallback=None: {
            "agent_iteration_warning_threshold": 0.50,
            "web_research_max_consecutive_failures": 3,
        }.get(key, fallback if fallback is not None else 10)

        call_count = [0]

        def make_response(**kwargs):
            call_count[0] += 1
            return _make_tool_call_response(
                "get_body_list", {}, f"tool_{call_count[0]}"
            )

        mock_provider.stream_message.side_effect = make_response

        events = []
        client.run_turn("Threshold test", on_event=lambda t, p: events.append((t, p)))

        messages = client.get_messages()
        warning_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "[SYSTEM] Warning: You have used" in m["content"]
        ]
        assert len(warning_msgs) == 1
        # The warning should mention iteration 5 of 10
        assert "5/10" in warning_msgs[0]["content"]

    def test_warning_injected_only_once(self):
        """Warning is injected only once even if many iterations follow."""
        client, mock_provider, mcp = _build_client()
        client._MAX_AGENT_ITERATIONS = 5

        call_count = [0]

        def make_response(**kwargs):
            call_count[0] += 1
            return _make_tool_call_response(
                "get_body_list", {}, f"tool_{call_count[0]}"
            )

        mock_provider.stream_message.side_effect = make_response

        events = []
        client.run_turn("Once test", on_event=lambda t, p: events.append((t, p)))

        messages = client.get_messages()
        warning_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "[SYSTEM] Warning: You have used" in m["content"]
        ]
        # Should be exactly 1 even though iterations 4 and 5 both exceed 80%
        assert len(warning_msgs) == 1


# ---------------------------------------------------------------------------
# TASK-224: Web research budget tracking
# ---------------------------------------------------------------------------

class TestAgentLoopWebResearchBudget:
    """Test: web research budget exhaustion after consecutive failures."""

    def test_budget_exhaustion_after_3_failures(self):
        """After 3 consecutive empty web_search results, budget message injected."""
        client, mock_provider, mcp = _build_client()

        # Each iteration: LLM calls web_search, gets empty results
        # After 3 failures, budget exhaustion message should appear
        call_count = [0]

        def make_response(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 3:
                return _make_tool_call_response(
                    "web_search", {"query": f"test {call_count[0]}"},
                    f"tool_{call_count[0]}",
                )
            return _make_text_response("I could not find results.")

        mock_provider.stream_message.side_effect = make_response

        # web_search returns empty results (success but no matches)
        mcp.execute_tool.return_value = {
            "status": "success",
            "results": [],
            "search_provider": "duckduckgo",
            "provider_configured": True,
            "diagnostic": "No results found.",
        }

        events = []
        client.run_turn(
            "Search for something",
            on_event=lambda t, p: events.append((t, p)),
        )

        messages = client.get_messages()
        budget_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "[SYSTEM] Web research budget exhausted" in m["content"]
        ]
        assert len(budget_msgs) == 1
        assert "3 consecutive" in budget_msgs[0]["content"]

    def test_budget_resets_on_success(self):
        """A successful web result resets the failure counter."""
        client, mock_provider, mcp = _build_client()

        call_count = [0]

        def make_response(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 4:
                return _make_tool_call_response(
                    "web_search", {"query": f"test {call_count[0]}"},
                    f"tool_{call_count[0]}",
                )
            return _make_text_response("Done.")

        mock_provider.stream_message.side_effect = make_response

        # First 2 calls fail, 3rd succeeds, 4th fails
        # Total consecutive failures never reach 3
        results = [
            {"status": "success", "results": []},                       # fail 1
            {"status": "success", "results": []},                       # fail 2
            {"status": "success", "results": [{"title": "Found it"}]},  # success -- reset
            {"status": "success", "results": []},                       # fail 1 again
        ]
        call_idx = [0]

        def execute_tool_side_effect(name, args):
            if name == "web_search" and call_idx[0] < len(results):
                r = results[call_idx[0]]
                call_idx[0] += 1
                return r
            return {"status": "success", "success": True}

        mcp.execute_tool.side_effect = execute_tool_side_effect

        events = []
        client.run_turn("Search test", on_event=lambda t, p: events.append((t, p)))

        messages = client.get_messages()
        budget_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "[SYSTEM] Web research budget exhausted" in m["content"]
        ]
        # Budget should NOT have been exhausted (reset after success)
        assert len(budget_msgs) == 0

    def test_budget_resets_on_non_web_tool(self):
        """A non-web tool call resets the web failure counter."""
        client, mock_provider, mcp = _build_client()

        call_count = [0]

        def make_response(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_tool_call_response("web_search", {"query": "test1"}, "t1")
            if call_count[0] == 2:
                return _make_tool_call_response("web_search", {"query": "test2"}, "t2")
            if call_count[0] == 3:
                # Non-web tool should reset the counter
                return _make_tool_call_response("get_body_list", {}, "t3")
            if call_count[0] == 4:
                return _make_tool_call_response("web_search", {"query": "test3"}, "t4")
            return _make_text_response("Done.")

        mock_provider.stream_message.side_effect = make_response

        # web_search always fails, but get_body_list succeeds
        def execute_tool_side_effect(name, args):
            if name == "web_search":
                return {"status": "success", "results": []}
            return {"status": "success", "success": True, "message": "done"}

        mcp.execute_tool.side_effect = execute_tool_side_effect

        events = []
        client.run_turn("Mixed test", on_event=lambda t, p: events.append((t, p)))

        messages = client.get_messages()
        budget_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "[SYSTEM] Web research budget exhausted" in m["content"]
        ]
        # Counter was reset by get_body_list, so never reached 3
        assert len(budget_msgs) == 0

    def test_budget_exhaustion_only_once(self):
        """Budget exhaustion message is injected only once per turn."""
        client, mock_provider, mcp = _build_client()

        call_count = [0]

        def make_response(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 5:
                return _make_tool_call_response(
                    "web_search", {"query": f"test {call_count[0]}"},
                    f"tool_{call_count[0]}",
                )
            return _make_text_response("Giving up.")

        mock_provider.stream_message.side_effect = make_response

        mcp.execute_tool.return_value = {
            "status": "success", "results": [],
        }

        events = []
        client.run_turn("Repeat search", on_event=lambda t, p: events.append((t, p)))

        messages = client.get_messages()
        budget_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "[SYSTEM] Web research budget exhausted" in m["content"]
        ]
        assert len(budget_msgs) == 1

    def test_error_status_counts_as_failure(self):
        """Web tool returning status='error' counts as a failure."""
        client, mock_provider, mcp = _build_client()

        call_count = [0]

        def make_response(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 3:
                return _make_tool_call_response(
                    "web_fetch", {"url": f"http://example.com/{call_count[0]}"},
                    f"tool_{call_count[0]}",
                )
            return _make_text_response("Failed.")

        mock_provider.stream_message.side_effect = make_response

        mcp.execute_tool.return_value = {
            "status": "error",
            "results": [],
            "error": "Connection refused",
        }

        events = []
        client.run_turn("Fetch test", on_event=lambda t, p: events.append((t, p)))

        messages = client.get_messages()
        budget_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "[SYSTEM] Web research budget exhausted" in m["content"]
        ]
        assert len(budget_msgs) == 1


# ---------------------------------------------------------------------------
# TASK-236: Empty assistant response detection and recovery
# ---------------------------------------------------------------------------

def _make_empty_response() -> LLMResponse:
    """Build an LLMResponse with empty content list."""
    resp = LLMResponse()
    resp.content = []
    resp.stop_reason = "end_turn"
    resp.usage = {"input_tokens": 10, "output_tokens": 0}
    resp.model = "mock-model"
    return resp


def _make_none_content_response() -> LLMResponse:
    """Build an LLMResponse with None content."""
    resp = LLMResponse()
    resp.content = None
    resp.stop_reason = "end_turn"
    resp.usage = {"input_tokens": 10, "output_tokens": 0}
    resp.model = "mock-model"
    return resp


def _make_empty_string_content_response() -> LLMResponse:
    """Build an LLMResponse with empty string content."""
    resp = LLMResponse()
    resp.content = ""
    resp.stop_reason = "end_turn"
    resp.usage = {"input_tokens": 10, "output_tokens": 0}
    resp.model = "mock-model"
    return resp


class TestAgentLoopEmptyResponseDetection:
    """TASK-236: Empty assistant response detection and recovery."""

    def test_empty_content_list_triggers_nudge(self):
        """First empty [] content triggers a retry with nudge message."""
        client, mock_provider, _mcp = _build_client()

        # First call: empty response; second call: real text
        mock_provider.stream_message.side_effect = [
            _make_empty_response(),
            _make_text_response("I'm back on track now."),
        ]

        events = []
        client.run_turn("Do something", on_event=lambda t, p: events.append((t, p)))

        # The loop should have completed successfully
        event_types = [e[0] for e in events]
        assert "done" in event_types

        messages = client.get_messages()
        # Should contain the nudge message
        nudge_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "previous response was empty" in m["content"]
        ]
        assert len(nudge_msgs) == 1

        # Should also contain the recovery text
        assistant_msgs = [
            m for m in messages
            if m.get("role") == "assistant"
            and isinstance(m.get("content"), list)
            and any(
                isinstance(b, dict) and b.get("type") == "text"
                and "back on track" in b.get("text", "")
                for b in m["content"]
            )
        ]
        assert len(assistant_msgs) == 1

    def test_two_consecutive_empty_responses_terminates(self):
        """Second consecutive empty response terminates the loop."""
        client, mock_provider, _mcp = _build_client()

        # Two empty responses in a row -- should terminate gracefully
        mock_provider.stream_message.side_effect = [
            _make_empty_response(),
            _make_empty_response(),
        ]

        events = []
        client.run_turn("Do something", on_event=lambda t, p: events.append((t, p)))

        event_types = [e[0] for e in events]
        assert "done" in event_types

        # Should contain termination text
        text_done_events = [
            e for e in events
            if e[0] == "text_done"
            and "empty responses" in e[1].get("full_text", "")
        ]
        assert len(text_done_events) == 1
        assert "Session terminated" in text_done_events[0][1]["full_text"]

    def test_non_empty_resets_counter(self):
        """A non-empty response between empties resets the counter."""
        client, mock_provider, _mcp = _build_client()

        mock_provider.stream_message.side_effect = [
            _make_empty_response(),                    # empty 1 -> nudge
            _make_text_response("Recovered."),         # non-empty -> reset
        ]

        events = []
        client.run_turn("Test reset", on_event=lambda t, p: events.append((t, p)))

        event_types = [e[0] for e in events]
        assert "done" in event_types

        # No termination message -- loop completed normally
        text_done_events = [
            e for e in events
            if e[0] == "text_done"
        ]
        # The "Recovered." text should be the last text_done
        assert any(
            "Recovered." in e[1].get("full_text", "")
            for e in text_done_events
        )
        # No "Session terminated" message
        assert not any(
            "Session terminated" in e[1].get("full_text", "")
            for e in text_done_events
        )

    def test_none_content_detected_as_empty(self):
        """None content is detected as empty response."""
        client, mock_provider, _mcp = _build_client()

        mock_provider.stream_message.side_effect = [
            _make_none_content_response(),
            _make_none_content_response(),
        ]

        events = []
        client.run_turn("Test none", on_event=lambda t, p: events.append((t, p)))

        # Should terminate after two consecutive empties
        text_done_events = [
            e for e in events
            if e[0] == "text_done"
            and "empty responses" in e[1].get("full_text", "")
        ]
        assert len(text_done_events) == 1

    def test_empty_string_detected_as_empty(self):
        """Empty string content is detected as empty response."""
        client, mock_provider, _mcp = _build_client()

        mock_provider.stream_message.side_effect = [
            _make_empty_string_content_response(),
            _make_empty_string_content_response(),
        ]

        events = []
        client.run_turn("Test empty string", on_event=lambda t, p: events.append((t, p)))

        text_done_events = [
            e for e in events
            if e[0] == "text_done"
            and "empty responses" in e[1].get("full_text", "")
        ]
        assert len(text_done_events) == 1

    def test_first_empty_triggers_nudge_not_termination(self):
        """First empty response triggers nudge, NOT termination."""
        client, mock_provider, _mcp = _build_client()

        # First empty, then normal text
        mock_provider.stream_message.side_effect = [
            _make_empty_response(),
            _make_text_response("Here's the answer."),
        ]

        events = []
        client.run_turn("Question", on_event=lambda t, p: events.append((t, p)))

        # NO termination message
        text_done_events = [
            e for e in events if e[0] == "text_done"
        ]
        assert not any(
            "Session terminated" in e[1].get("full_text", "")
            for e in text_done_events
        )
        # Nudge WAS injected
        messages = client.get_messages()
        nudge_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "previous response was empty" in m["content"]
        ]
        assert len(nudge_msgs) == 1


# ---------------------------------------------------------------------------
# TASK-234: Progress tracker integration in agent loop
# ---------------------------------------------------------------------------

class TestAgentLoopProgressTracker:
    """TASK-234: Progress tracker integration tests."""

    def test_progress_tracker_reset_each_turn(self):
        """Progress tracker is reset at the start of each turn."""
        client, mock_provider, mcp = _build_client()

        # First turn: one tool call
        mock_provider.stream_message.side_effect = [
            _make_tool_call_response("create_box", {"width": 1}, "t1"),
            _make_text_response("Created box."),
        ]
        client.run_turn("Create box", on_event=lambda t, p: None)

        # Check tracker was incremented
        assert client._progress_tracker.productive_count == 1

        # Second turn: tracker should be reset
        mock_provider.stream_message.side_effect = [
            _make_text_response("Nothing to do."),
        ]
        client.run_turn("Hello", on_event=lambda t, p: None)

        # After reset at start of second turn, and no tool calls, should be 0
        assert client._progress_tracker.productive_count == 0

    def test_thrashing_warning_injected(self):
        """Thrashing warning is injected when ratio exceeds threshold."""
        client, mock_provider, mcp = _build_client()

        # Force a low threshold for testing
        client._progress_tracker = __import__(
            "ai.progress_tracker", fromlist=["ProgressTracker"]
        ).ProgressTracker(
            thrashing_ratio_threshold=0.5,
            min_calls_for_warning=5,
        )

        # Create 10 undo calls then a text response
        call_count = [0]

        def make_response(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 10:
                return _make_tool_call_response(
                    "undo", {}, f"tool_{call_count[0]}"
                )
            return _make_text_response("Done thrashing.")

        mock_provider.stream_message.side_effect = make_response

        events = []
        client.run_turn("Undo everything", on_event=lambda t, p: events.append((t, p)))

        # Check that a warning event was emitted
        warning_events = [
            e for e in events
            if e[0] == "warning"
            and "THRASHING WARNING" in e[1].get("message", "")
        ]
        assert len(warning_events) >= 1

    def test_productive_calls_tracked(self):
        """Productive tool calls are correctly tracked."""
        client, mock_provider, mcp = _build_client()

        mock_provider.stream_message.side_effect = [
            _make_tool_call_response("create_box", {"width": 1}, "t1"),
            _make_tool_call_response("extrude", {"distance": 5}, "t2"),
            _make_text_response("Done."),
        ]

        client.run_turn("Build", on_event=lambda t, p: None)

        assert client._progress_tracker.productive_count == 2
        assert client._progress_tracker.thrashing_count == 0

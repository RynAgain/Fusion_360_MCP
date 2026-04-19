"""Tests for web/events.py -- Socket.IO event handlers.

TASK-073: Covers user_message, cancel, connect/disconnect_fusion,
clear_history, orchestration events, and the emitter callback factory.
"""
import os
import threading

import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Unit tests for module-level helpers (no Flask app needed)
# ---------------------------------------------------------------------------


class TestCancelEvent:
    """Tests for the cancel event and get_cancel_event."""

    def test_get_cancel_event_returns_threading_event(self):
        from web.events import get_cancel_event

        event = get_cancel_event()
        assert isinstance(event, threading.Event)

    def test_cancel_event_starts_clear(self):
        from web.events import get_cancel_event

        event = get_cancel_event()
        event.clear()
        assert not event.is_set()

    def test_cancel_event_can_be_set_and_cleared(self):
        from web.events import get_cancel_event

        event = get_cancel_event()
        event.set()
        assert event.is_set()
        event.clear()
        assert not event.is_set()


class TestMakeSocketIOEmitter:
    """Tests for _make_socketio_emitter callback factory."""

    def _make_emitter(self):
        """Create an emitter backed by a MagicMock SocketIO."""
        from web import events

        mock_sio = MagicMock()
        original = events._socketio
        events._socketio = mock_sio
        emitter = events._make_socketio_emitter()
        events._socketio = original  # restore immediately
        return emitter, mock_sio

    def test_emitter_emits_event_type_directly(self):
        emitter, sio = self._make_emitter()
        emitter("text_delta", {"content": "hello"})
        sio.emit.assert_any_call("text_delta", {"content": "hello"})

    def test_emitter_emits_text_done(self):
        emitter, sio = self._make_emitter()
        emitter("text_done", {"text": "final"})
        sio.emit.assert_any_call("text_done", {"text": "final"})

    def test_emitter_forwards_screenshot_from_tool_result(self):
        emitter, sio = self._make_emitter()
        payload = {"result": {"image_base64": "abc123", "format": "png"}}
        emitter("tool_result", payload)

        calls = sio.emit.call_args_list
        assert any(c == call("tool_result", payload) for c in calls)
        assert any(
            c == call("screenshot", {"image_base64": "abc123", "format": "png"})
            for c in calls
        )

    def test_emitter_no_screenshot_when_no_image_in_tool_result(self):
        emitter, sio = self._make_emitter()
        payload = {"result": {"data": "no image here"}}
        emitter("tool_result", payload)

        calls = sio.emit.call_args_list
        assert not any(c[0][0] == "screenshot" for c in calls)

    def test_emitter_translates_usage_to_token_usage(self):
        emitter, sio = self._make_emitter()
        payload = {"input": 100, "output": 50}
        emitter("usage", payload)

        calls = sio.emit.call_args_list
        assert any(c == call("token_usage", payload) for c in calls)

    def test_emitter_translates_condensing_to_status_update(self):
        emitter, sio = self._make_emitter()
        emitter("condensing", {"message": "Condensing context..."})

        calls = sio.emit.call_args_list
        assert any(
            c == call("status_update", {"type": "info", "message": "Condensing context..."})
            for c in calls
        )

    def test_emitter_translates_condensed_to_status_update(self):
        emitter, sio = self._make_emitter()
        emitter("condensed", {"message": "Context condensed"})

        calls = sio.emit.call_args_list
        assert any(
            c == call("status_update", {"type": "success", "message": "Context condensed"})
            for c in calls
        )

    def test_emitter_translates_warning_to_status_update(self):
        emitter, sio = self._make_emitter()
        emitter("warning", {"message": "Repetition detected"})

        calls = sio.emit.call_args_list
        assert any(
            c == call("status_update", {"type": "warning", "message": "Repetition detected"})
            for c in calls
        )


# ---------------------------------------------------------------------------
# Socket.IO test-client integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def app_and_socketio():
    """Create a test Flask app with Socket.IO by mocking heavy dependencies.

    Patches FusionBridge, MCPServer, ClaudeClient, and helper functions
    so that create_app() succeeds without any real infrastructure.
    """
    with patch("fusion.bridge.FusionBridge") as MockBridge, \
         patch("mcp.server.MCPServer") as MockMCPServer, \
         patch("ai.claude_client.ClaudeClient") as MockClaudeClient, \
         patch("ai.rules_loader.create_example_rules"):

        mock_bridge = MagicMock()
        mock_bridge.connected = False
        mock_bridge.connect.return_value = {"status": "error", "message": "mocked"}
        MockBridge.return_value = mock_bridge

        mock_mcp = MagicMock()
        mock_mcp.get_tool_names.return_value = ["tool1", "tool2"]
        MockMCPServer.return_value = mock_mcp

        mock_client = MagicMock()
        mock_client.get_messages.return_value = []
        mock_client.token_usage = {"input": 0, "output": 0}
        # Ensure _turn_lock.locked() returns False so handle_user_message
        # does not treat every message as a mid-turn injection.
        mock_client._turn_lock.locked.return_value = False
        MockClaudeClient.return_value = mock_client

        # Force threading mode so the test client works synchronously
        os.environ["ARTIFEX360_ASYNC_MODE"] = "threading"

        from web.app import create_app
        app, socketio = create_app()
        app.config["TESTING"] = True

        yield app, socketio, mock_bridge, mock_mcp, mock_client

        os.environ.pop("ARTIFEX360_ASYNC_MODE", None)


class TestConnectEvent:
    """Tests for the Socket.IO connect event."""

    def test_connect_emits_status_update(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        test_client = socketio.test_client(app)
        received = test_client.get_received()

        status_events = [r for r in received if r["name"] == "status_update"]
        assert len(status_events) >= 1
        payload = status_events[0]["args"][0]
        assert payload["type"] == "connection"
        assert "tools_count" in payload

        test_client.disconnect()


class TestUserMessageEvent:
    """Tests for the user_message event handler."""

    def test_empty_message_returns_error(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()  # clear connect events

        tc.emit("user_message", {"message": ""})
        received = tc.get_received()

        error_events = [r for r in received if r["name"] == "error"]
        assert len(error_events) > 0
        assert "Empty message" in error_events[0]["args"][0]["message"]

        tc.disconnect()

    def test_none_data_returns_error(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("user_message", None)
        received = tc.get_received()

        error_events = [r for r in received if r["name"] == "error"]
        assert len(error_events) > 0

        tc.disconnect()

    def test_whitespace_only_message_returns_error(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("user_message", {"message": "   \n\t  "})
        received = tc.get_received()

        error_events = [r for r in received if r["name"] == "error"]
        assert len(error_events) > 0

        tc.disconnect()

    def test_valid_message_emits_thinking_start(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("user_message", {"message": "Hello world"})
        received = tc.get_received()

        thinking_events = [r for r in received if r["name"] == "thinking_start"]
        assert len(thinking_events) > 0

        tc.disconnect()


class TestCancelSocketEvent:
    """Tests for the cancel Socket.IO event handler."""

    def test_cancel_emits_status_update(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("cancel")
        received = tc.get_received()

        status_events = [r for r in received if r["name"] == "status_update"]
        assert len(status_events) > 0
        assert status_events[0]["args"][0]["type"] == "cancel"
        assert "cancel" in status_events[0]["args"][0]["message"].lower()

        tc.disconnect()

    def test_cancel_sets_cancel_event_flag(self, app_and_socketio):
        from web.events import get_cancel_event, _cancel_events

        app, socketio, bridge, mcp, client = app_and_socketio

        tc = socketio.test_client(app)
        tc.emit("cancel")

        # TASK-102: Cancel event is now per-session.  Check that at least
        # one cancel event in the registry is set.
        any_set = any(evt.is_set() for evt in _cancel_events.values())
        assert any_set, "Expected at least one per-session cancel event to be set"

        # cleanup
        for evt in _cancel_events.values():
            evt.clear()

        tc.disconnect()


class TestConnectFusionEvent:
    """Tests for the connect_fusion event handler."""

    def test_connect_fusion_calls_bridge_connect(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        bridge.connect.return_value = {"status": "success", "message": "Connected"}
        bridge.connected = True

        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("connect_fusion")
        received = tc.get_received()

        # bridge.connect() is called once in create_app and once here
        assert bridge.connect.call_count >= 2

        status_events = [r for r in received if r["name"] == "status_update"]
        assert len(status_events) > 0
        assert status_events[0]["args"][0]["type"] == "fusion_connection"

        tc.disconnect()


class TestDisconnectFusionEvent:
    """Tests for the disconnect_fusion event handler."""

    def test_disconnect_fusion_calls_bridge_disconnect(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("disconnect_fusion")
        received = tc.get_received()

        bridge.disconnect.assert_called()

        status_events = [r for r in received if r["name"] == "status_update"]
        assert len(status_events) > 0
        payload = status_events[0]["args"][0]
        assert payload["type"] == "fusion_connection"
        assert payload["fusion_connected"] is False

        tc.disconnect()


class TestClearHistoryEvent:
    """Tests for the clear_history event handler."""

    def test_clear_history_calls_client_methods(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("clear_history")
        received = tc.get_received()

        client.clear_history.assert_called_once()
        client.new_conversation.assert_called_once()

        status_events = [r for r in received if r["name"] == "status_update"]
        assert len(status_events) > 0
        assert status_events[0]["args"][0]["type"] == "history"

        tc.disconnect()


class TestToolConfirmationEvent:
    """Tests for the tool_confirmation event handler."""

    def test_tool_confirmation_does_not_error(self, app_and_socketio):
        """tool_confirmation is a stub -- it should not crash."""
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("tool_confirmation", {"allowed": True})
        received = tc.get_received()

        # No error events expected
        error_events = [r for r in received if r["name"] == "error"]
        assert len(error_events) == 0

        tc.disconnect()


class TestCreateOrchestratedPlanEvent:
    """Tests for the create_orchestrated_plan event handler."""

    def test_missing_title_returns_error(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("create_orchestrated_plan", {"steps": ["step1"]})
        received = tc.get_received()

        error_events = [r for r in received if r["name"] == "error"]
        assert len(error_events) > 0
        assert "title" in error_events[0]["args"][0]["message"].lower()

        tc.disconnect()

    def test_missing_steps_returns_error(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("create_orchestrated_plan", {"title": "My Plan"})
        received = tc.get_received()

        error_events = [r for r in received if r["name"] == "error"]
        assert len(error_events) > 0

        tc.disconnect()

    def test_empty_data_returns_error(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("create_orchestrated_plan", {})
        received = tc.get_received()

        error_events = [r for r in received if r["name"] == "error"]
        assert len(error_events) > 0

        tc.disconnect()

    def test_none_data_returns_error(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("create_orchestrated_plan", None)
        received = tc.get_received()

        error_events = [r for r in received if r["name"] == "error"]
        assert len(error_events) > 0

        tc.disconnect()


class TestExecuteSubtaskEvent:
    """Tests for the execute_subtask event handler."""

    def test_missing_step_index_returns_error(self, app_and_socketio):
        app, socketio, bridge, mcp, client = app_and_socketio
        tc = socketio.test_client(app)
        tc.get_received()

        tc.emit("execute_subtask", {})
        received = tc.get_received()

        error_events = [r for r in received if r["name"] == "error"]
        assert len(error_events) > 0
        assert "step_index" in error_events[0]["args"][0]["message"]

        tc.disconnect()

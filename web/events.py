"""
web/events.py
Socket.IO event handlers for real-time communication between the
web client and the Artifex360 backend.

Client -> Server events:
    user_message, connect_fusion, disconnect_fusion, clear_history, cancel

Server -> Client events:
    text_delta, text_done, tool_call, tool_result, error, done,
    status_update, thinking_start, thinking_stop
"""

import logging
import threading

from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

# Will be set by register()
_socketio: SocketIO | None = None

# TASK-103: Maximum allowed message length (characters).
_MAX_MESSAGE_LENGTH = 100_000  # 100K chars

# TASK-102: Per-session cancellation events.  Replaces the single global
# _cancel_event so that one user cancelling does not affect other sessions.
_cancel_events: dict[str, threading.Event] = {}
_cancel_events_lock = threading.Lock()


def register(socketio: SocketIO) -> None:
    """
    Register all Socket.IO event handlers on the given SocketIO instance.
    Called once from create_app().
    """
    global _socketio
    _socketio = socketio

    @socketio.on("connect")
    def handle_connect():
        logger.info("WebSocket client connected")
        from web.app import bridge, mcp_server
        socketio.emit("status_update", {
            "type": "connection",
            "message": "Connected to server",
            "fusion_connected": bridge.connected,
            "tools_count": len(mcp_server.get_tool_names()),
        })

    @socketio.on("disconnect")
    def handle_disconnect():
        logger.info("WebSocket client disconnected")
        # TASK-102: Clean up per-session cancel event on disconnect
        try:
            from flask import request as _req
            sid = getattr(_req, "sid", None)
            if sid:
                with _cancel_events_lock:
                    _cancel_events.pop(sid, None)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    @socketio.on("user_message")
    def handle_user_message(data):
        """
        Handle an incoming user message.
        Runs the Claude agent loop on a background thread and streams
        events back over Socket.IO.

        If a turn is currently running, the message is queued for mid-turn
        injection instead of starting a new turn.
        """
        message = (data or {}).get("message", "").strip()
        if not message:
            socketio.emit("error", {"message": "Empty message received."})
            return

        # TASK-103: Reject messages that exceed the length limit
        if len(message) > _MAX_MESSAGE_LENGTH:
            socketio.emit("error", {
                "message": f"Message too long ({len(message)} chars, max {_MAX_MESSAGE_LENGTH})",
            })
            return

        logger.info("user_message: %s", message[:120])

        # If a turn is currently running, queue the message for mid-turn injection
        from web.app import claude_client as cc
        if cc and hasattr(cc, '_turn_lock') and cc._turn_lock.locked():
            cc.message_queue.enqueue(message)
            socketio.emit("status_update", {
                "type": "info",
                "message": "Message queued -- will be injected into current turn",
            })
            return

        socketio.emit("thinking_start", {})

        # Run the Claude agent loop in a background greenlet
        socketio.start_background_task(_run_claude_loop, message)

    # ------------------------------------------------------------------
    # Fusion bridge control
    # ------------------------------------------------------------------

    @socketio.on("connect_fusion")
    def handle_connect_fusion(_data=None):
        from web.app import bridge
        result = bridge.connect()
        socketio.emit("status_update", {
            "type": "fusion_connection",
            "message": result.get("message", ""),
            "fusion_connected": bridge.connected,
        })

    @socketio.on("disconnect_fusion")
    def handle_disconnect_fusion(_data=None):
        from web.app import bridge
        bridge.disconnect()
        socketio.emit("status_update", {
            "type": "fusion_connection",
            "message": "Disconnected from Fusion 360.",
            "fusion_connected": False,
        })

    # ------------------------------------------------------------------
    # Conversation management
    # ------------------------------------------------------------------

    @socketio.on("clear_history")
    def handle_clear_history(_data=None):
        from web.app import claude_client
        claude_client.clear_history()
        claude_client.new_conversation()
        socketio.emit("status_update", {
            "type": "history",
            "message": "Conversation history cleared. New conversation started.",
        })

    # TASK-031: Tool confirmation event handler.
    # TASK-129: Tool confirmation is currently a no-op.
    # The UI shows Allow/Deny buttons but the tool has already executed
    # by the time this event arrives. To properly gate execution:
    #   1. The agent loop must pause before executing destructive tools
    #   2. Emit a 'tool_confirmation_required' event and wait
    #   3. Resume or skip based on this handler's response
    # This requires refactoring _run_turn_inner() to support async gates.
    @socketio.on("tool_confirmation")
    def handle_tool_confirmation(data):
        allowed = (data or {}).get("allowed", False)
        logger.info("Tool confirmation received: allowed=%s", allowed)

    @socketio.on("cancel")
    def handle_cancel(_data=None):
        # TASK-015 + TASK-102: Signal cancellation to the running agent loop
        logger.info("Cancel requested by user")
        try:
            from flask import request as _req
            sid = getattr(_req, "sid", None)
        except Exception:
            sid = None
        get_cancel_event(sid).set()
        socketio.emit("status_update", {
            "type": "cancel",
            "message": "Cancellation requested. The agent will stop after the current operation.",
        })

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    @socketio.on("create_orchestrated_plan")
    def handle_create_orchestrated_plan(data):
        """Create an orchestrated design plan from client payload."""
        from web.app import claude_client
        data = data or {}
        title = data.get("title")
        steps = data.get("steps")
        if not title or not steps:
            socketio.emit("error", {"message": "Both 'title' and 'steps' are required"})
            return
        try:
            claude_client.create_orchestrated_plan(title, steps)
            socketio.emit("orchestrated_plan_created", {
                "plan_summary": claude_client.task_manager.get_plan_summary(),
            })
        except Exception as exc:
            logger.exception("Error creating orchestrated plan")
            socketio.emit("error", {"message": "An internal error occurred. Check server logs for details."})

    @socketio.on("execute_next_subtask")
    def handle_execute_next_subtask(data=None):
        """Execute the next ready subtask in the orchestrated plan."""
        from web.app import claude_client
        data = data or {}
        additional_instructions = data.get("additional_instructions", "")

        def _run():
            try:
                result = claude_client.execute_next_subtask(
                    additional_instructions=additional_instructions,
                )
                socketio.emit("subtask_result", result)
            except Exception as exc:
                logger.exception("Error executing next subtask")
                socketio.emit("error", {"message": "An internal error occurred. Check server logs for details."})

        socketio.start_background_task(_run)

    @socketio.on("execute_subtask")
    def handle_execute_subtask(data):
        """Execute a specific subtask step."""
        from web.app import claude_client
        data = data or {}
        step_index = data.get("step_index")
        additional_instructions = data.get("additional_instructions", "")
        if step_index is None:
            socketio.emit("error", {"message": "'step_index' is required"})
            return

        def _run():
            try:
                result = claude_client.execute_subtask(
                    step_index, additional_instructions=additional_instructions,
                )
                socketio.emit("subtask_result", result)
            except Exception as exc:
                logger.exception("Error executing subtask %s", step_index)
                socketio.emit("error", {"message": "An internal error occurred. Check server logs for details."})

        socketio.start_background_task(_run)

    @socketio.on("execute_full_plan")
    def handle_execute_full_plan(data=None):
        """Execute all remaining steps in the orchestrated plan."""
        from web.app import claude_client
        data = data or {}
        additional_instructions = data.get("additional_instructions", "")

        def _run():
            try:
                result = claude_client.execute_full_plan(
                    additional_instructions=additional_instructions,
                )
                socketio.emit("orchestration_completed", result)
            except Exception as exc:
                logger.exception("Error executing full plan")
                socketio.emit("error", {"message": "An internal error occurred. Check server logs for details."})

        socketio.start_background_task(_run)

    @socketio.on("get_orchestration_status")
    def handle_get_orchestration_status(_data=None):
        """Return current orchestration status to the client."""
        from web.app import claude_client
        try:
            status = claude_client.get_orchestration_status()
            socketio.emit("orchestration_status", status)
        except Exception as exc:
            logger.exception("Error getting orchestration status")
            socketio.emit("error", {"message": "An internal error occurred. Check server logs for details."})


# ---------------------------------------------------------------------------
# Background task — Claude agent loop
# ---------------------------------------------------------------------------

def _make_socketio_emitter():
    """
    Build a callback function that translates ClaudeClient events
    into Socket.IO emissions.

    The returned function has signature (event_type: str, payload: dict).
    Uses socketio.emit() which is safe to call from background threads/greenlets.
    """
    sio = _socketio

    def emitter(event_type: str, payload: dict) -> None:
        # event_type values match the EventType constants in claude_client:
        #   text_delta, text_done, tool_call, tool_result, error, done, usage
        #   condensing, condensed, warning  (context manager / repetition detector)
        sio.emit(event_type, payload)

        # If a tool_result contains screenshot image data, emit it as a
        # separate 'screenshot' event so the browser can display it inline.
        if event_type == "tool_result":
            result = (payload or {}).get("result", {})
            if isinstance(result, dict) and "image_base64" in result:
                sio.emit("screenshot", {
                    "image_base64": result["image_base64"],
                    "format": result.get("format", "png"),
                })

        # Emit token usage as a dedicated 'token_usage' event for the UI
        elif event_type == "usage":
            sio.emit("token_usage", payload)

        # Context condensation events
        elif event_type == "condensing":
            sio.emit("status_update", {
                "type": "info",
                "message": (payload or {}).get("message", "Condensing..."),
            })

        elif event_type == "condensed":
            sio.emit("status_update", {
                "type": "success",
                "message": (payload or {}).get("message", "Context condensed"),
            })

        # Repetition / general warning events
        elif event_type == "warning":
            sio.emit("status_update", {
                "type": "warning",
                "message": (payload or {}).get("message", ""),
            })

        # Reasoning/thinking events (Qwen 3.x, DeepSeek R1)
        elif event_type == "reasoning_delta":
            sio.emit("reasoning_delta", payload)

        elif event_type == "reasoning_complete":
            sio.emit("reasoning_complete", payload)

        # TASK-228: Context window adequacy warnings
        elif event_type == "context_window_warning":
            level = (payload or {}).get("level", "warning")
            reasons = (payload or {}).get("reasons", [])
            sio.emit("status_update", {
                "type": "warning" if level == "warning" else "error",
                "message": (
                    f"[Context Window {level.upper()}] "
                    + ("; ".join(reasons) if reasons else "Context window may be too small")
                ),
            })

        # TASK-228: Runtime context pressure events
        elif event_type == "context_pressure":
            level = (payload or {}).get("level", "warning")
            sio.emit("status_update", {
                "type": "warning" if level == "warning" else "error",
                "message": (payload or {}).get(
                    "message",
                    "Context pressure detected",
                ),
            })

    return emitter


def _run_claude_loop(message: str) -> None:
    """
    Execute the Claude agent loop for a single user message.
    Runs inside a background greenlet spawned by socketio.start_background_task().
    After the turn completes, auto-saves the conversation to disk.

    TASK-014: Wrapped in try/except/finally so the user always receives
    feedback -- even if the agent loop crashes or context is exhausted.

    TASK-015: Checks _cancel_event between operations so the user can
    abort a long-running turn.
    """
    from web.app import claude_client
    from web.routes import conversation_manager

    emitter = _make_socketio_emitter()

    # TASK-015 + TASK-102: Clear any stale cancellation signal before starting
    cancel_evt = get_cancel_event()
    cancel_evt.clear()

    try:
        # TASK-015: Check cancellation before starting
        if cancel_evt.is_set():
            _socketio.emit("claude_response", {
                "message": "[Cancelled] Operation cancelled by user.",
            })
            return

        # run_turn is the synchronous public method; we call it directly
        # because we are already in a background greenlet.
        # Pass the cancel event so the agent loop can check it between
        # tool calls and at the start of each iteration.
        claude_client.run_turn(message, on_event=emitter, cancel_event=cancel_evt)

    except Exception:
        # TASK-014: Catch ALL exceptions so the user never gets silence
        # TASK-055: Full traceback is logged server-side only; clients
        # receive a generic error message to avoid leaking internals.
        logger.exception("Error in Claude agent loop")

        # Emit error events so the UI always shows something
        _socketio.emit("claude_error", {
            "message": "An internal error occurred. Check server logs for details.",
        })
        _socketio.emit("claude_response", {
            "message": (
                "[System Error] The agent encountered an unexpected error. "
                "Please try again or start a new conversation."
            ),
        })

    finally:
        # TASK-014: Always emit turn_complete / thinking_stop / done
        # so the UI is never left in a "thinking" spinner state.
        _socketio.emit("thinking_stop", {})
        _socketio.emit("turn_complete", {})
        _socketio.emit("done", {})

        # TASK-015: Clear cancellation flag after the turn ends
        cancel_evt.clear()

    # Auto-save conversation after each completed turn
    try:
        meta = conversation_manager.save(
            conversation_id=claude_client.get_conversation_id(),
            messages=claude_client.get_messages(),
        )
        _socketio.emit("conversation_saved", meta)
        logger.info("Auto-saved conversation %s", meta.get("id"))
    except Exception as exc:
        logger.error("Failed to auto-save conversation: %s", exc)


def get_cancel_event(sid: str = None) -> threading.Event:
    """Get or create a cancel event for a session.

    TASK-015 + TASK-102: Per-session cancellation events.  When *sid* is
    ``None`` the ``"__default__"`` event is returned (backwards compatible
    with code that doesn't pass a session id).
    """
    if sid is None:
        sid = "__default__"
    with _cancel_events_lock:
        if sid not in _cancel_events:
            _cancel_events[sid] = threading.Event()
        return _cancel_events[sid]

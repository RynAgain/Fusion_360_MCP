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
import traceback

from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

# Will be set by register()
_socketio: SocketIO | None = None

# TASK-015: Module-level cancellation event.  Set by the cancel handler,
# checked inside the agent loop between iterations / tool calls.
_cancel_event = threading.Event()


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
            "fusion_connected": bridge.is_connected() and not bridge.simulation_mode,
            "simulation_mode": bridge.simulation_mode,
            "tools_count": len(mcp_server.get_tool_names()),
        })

    @socketio.on("disconnect")
    def handle_disconnect():
        logger.info("WebSocket client disconnected")

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    @socketio.on("user_message")
    def handle_user_message(data):
        """
        Handle an incoming user message.
        Runs the Claude agent loop on a background thread and streams
        events back over Socket.IO.
        """
        message = (data or {}).get("message", "").strip()
        if not message:
            socketio.emit("error", {"message": "Empty message received."})
            return

        logger.info("user_message: %s", message[:120])
        socketio.emit("thinking_start", {})

        # Run the Claude agent loop in a background greenlet
        socketio.start_background_task(_run_claude_loop, message)

    # ------------------------------------------------------------------
    # Fusion bridge control
    # ------------------------------------------------------------------

    @socketio.on("connect_fusion")
    def handle_connect_fusion(_data=None):
        from web.app import bridge
        bridge._forced_sim = False        # Reset forced simulation
        bridge.simulation_mode = False    # Reset simulation mode
        result = bridge.connect()
        socketio.emit("status_update", {
            "type": "fusion_connection",
            "message": result.get("message", ""),
            "fusion_connected": bridge.is_connected() and not bridge.simulation_mode,
            "simulation_mode": bridge.simulation_mode,
        })

    @socketio.on("disconnect_fusion")
    def handle_disconnect_fusion(_data=None):
        from web.app import bridge
        bridge.disconnect()
        socketio.emit("status_update", {
            "type": "fusion_connection",
            "message": "Disconnected from Fusion 360.",
            "fusion_connected": False,
            "simulation_mode": bridge.simulation_mode,
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
    # TODO: The backend gating logic (actually blocking tool execution until
    # the user responds) is complex and not yet implemented.  For now we
    # just receive the confirmation event and log it.
    @socketio.on("tool_confirmation")
    def handle_tool_confirmation(data):
        allowed = (data or {}).get("allowed", False)
        logger.info("Tool confirmation received: allowed=%s", allowed)

    @socketio.on("cancel")
    def handle_cancel(_data=None):
        # TASK-015: Signal cancellation to the running agent loop
        logger.info("Cancel requested by user")
        _cancel_event.set()
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
            logger.error("Error creating orchestrated plan: %s", exc)
            socketio.emit("error", {"message": str(exc)})

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
                logger.error("Error executing next subtask: %s", exc)
                socketio.emit("error", {"message": str(exc)})

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
                logger.error("Error executing subtask %s: %s", step_index, exc)
                socketio.emit("error", {"message": str(exc)})

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
                logger.error("Error executing full plan: %s", exc)
                socketio.emit("error", {"message": str(exc)})

        socketio.start_background_task(_run)

    @socketio.on("get_orchestration_status")
    def handle_get_orchestration_status(_data=None):
        """Return current orchestration status to the client."""
        from web.app import claude_client
        try:
            status = claude_client.get_orchestration_status()
            socketio.emit("orchestration_status", status)
        except Exception as exc:
            logger.error("Error getting orchestration status: %s", exc)
            socketio.emit("error", {"message": str(exc)})


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

    # TASK-015: Clear any stale cancellation signal before starting
    _cancel_event.clear()

    try:
        # TASK-015: Check cancellation before starting
        if _cancel_event.is_set():
            _socketio.emit("claude_response", {
                "message": "[Cancelled] Operation cancelled by user.",
            })
            return

        # run_turn is the synchronous public method; we call it directly
        # because we are already in a background greenlet.
        claude_client.run_turn(message, on_event=emitter)

    except Exception as exc:
        # TASK-014: Catch ALL exceptions so the user never gets silence
        logger.exception("Error in Claude agent loop")
        tb_str = traceback.format_exc()
        logger.error("Full traceback:\n%s", tb_str)

        # Emit error events so the UI always shows something
        _socketio.emit("claude_error", {"message": str(exc), "traceback": tb_str})
        _socketio.emit("claude_response", {
            "message": (
                f"[System Error] The agent encountered an error: {exc}. "
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
        _cancel_event.clear()

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


def get_cancel_event() -> threading.Event:
    """Return the module-level cancellation event.

    TASK-015: The claude_client can import and check this event between
    tool calls to support cooperative cancellation.
    """
    return _cancel_event

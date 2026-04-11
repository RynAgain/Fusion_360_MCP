"""
web/events.py
Socket.IO event handlers for real-time communication between the
web client and the Fusion 360 MCP backend.

Client -> Server events:
    user_message, connect_fusion, disconnect_fusion, clear_history, cancel

Server -> Client events:
    text_delta, text_done, tool_call, tool_result, error, done,
    status_update, thinking_start, thinking_stop
"""

import logging

from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

# Will be set by register()
_socketio: SocketIO | None = None


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

    @socketio.on("cancel")
    def handle_cancel(_data=None):
        # Stub — cancellation support will be added later
        logger.info("Cancel requested (not yet implemented)")
        socketio.emit("status_update", {
            "type": "cancel",
            "message": "Cancel requested (not yet implemented).",
        })


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
    """
    from web.app import claude_client
    from web.routes import conversation_manager

    emitter = _make_socketio_emitter()

    try:
        # _run_turn is the synchronous inner method; we call it directly
        # because we are already in a background greenlet.
        claude_client._run_turn(message, on_event=emitter)
    except Exception as exc:
        logger.exception("Error in Claude agent loop")
        _socketio.emit("error", {"message": f"Agent error: {exc}"})

    _socketio.emit("thinking_stop", {})
    _socketio.emit("done", {})

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

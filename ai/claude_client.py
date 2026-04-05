"""
ai/claude_client.py
Anthropic Claude API client with MCP tool-use support.
Handles multi-turn conversation, tool call loops, and streams events
back to the UI via a callback.
"""

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try to import the Anthropic SDK
# ---------------------------------------------------------------------------
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic package not installed. Run: pip install anthropic")


# ---------------------------------------------------------------------------
# Event types emitted to the UI callback
# ---------------------------------------------------------------------------
class EventType:
    TEXT_DELTA   = "text_delta"       # partial text from Claude
    TEXT_DONE    = "text_done"        # full assistant text block finished
    TOOL_CALL    = "tool_call"        # Claude is calling a tool
    TOOL_RESULT  = "tool_result"      # result returned to Claude
    ERROR        = "error"            # something went wrong
    DONE         = "done"             # entire turn finished


class ClaudeClient:
    """
    Wraps the Anthropic Messages API with tool-use (MCP) support.

    Usage:
        client = ClaudeClient(settings, mcp_server)
        client.send_message("Create a 5cm radius cylinder", on_event=my_callback)

    The on_event callback receives (event_type: str, payload: dict).
    All network I/O runs on a background thread so the UI stays responsive.
    """

    def __init__(self, settings, mcp_server):
        self.settings = settings
        self.mcp_server = mcp_server
        self.conversation_history: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_message(
        self,
        user_text: str,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        """
        Send a user message to Claude in a background thread.
        on_event is called with (EventType, payload_dict) as things happen.
        """
        thread = threading.Thread(
            target=self._run_turn,
            args=(user_text, on_event),
            daemon=True,
        )
        thread.start()

    def clear_history(self) -> None:
        """Reset the conversation history."""
        with self._lock:
            self.conversation_history.clear()

    # ------------------------------------------------------------------
    # Internal turn execution
    # ------------------------------------------------------------------

    def _emit(self, on_event, event_type: str, payload: dict) -> None:
        if on_event:
            try:
                on_event(event_type, payload)
            except Exception as exc:
                logger.warning("on_event callback raised: %s", exc)

    def _run_turn(
        self,
        user_text: str,
        on_event: Callable[[str, dict], None] | None,
    ) -> None:
        """Full agentic loop: send → handle tool calls → send results → repeat."""

        if not ANTHROPIC_AVAILABLE:
            self._emit(on_event, EventType.ERROR, {
                "message": "anthropic package is not installed. Run: pip install anthropic"
            })
            self._emit(on_event, EventType.DONE, {})
            return

        api_key = self.settings.api_key
        if not api_key:
            self._emit(on_event, EventType.ERROR, {
                "message": "No Anthropic API key configured. Open Settings and enter your key."
            })
            self._emit(on_event, EventType.DONE, {})
            return

        client = anthropic.Anthropic(api_key=api_key)

        # Append user message to history
        with self._lock:
            self.conversation_history.append({"role": "user", "content": user_text})
            messages = list(self.conversation_history)

        # Agentic loop — keep going while Claude wants to call tools
        while True:
            try:
                response = client.messages.create(
                    model=self.settings.model,
                    max_tokens=self.settings.max_tokens,
                    system=self.settings.system_prompt,
                    tools=self.mcp_server.tool_definitions,
                    messages=messages,
                )
            except anthropic.AuthenticationError:
                self._emit(on_event, EventType.ERROR, {
                    "message": "Invalid Anthropic API key. Please check your settings."
                })
                self._emit(on_event, EventType.DONE, {})
                return
            except anthropic.RateLimitError:
                self._emit(on_event, EventType.ERROR, {
                    "message": "Anthropic rate limit hit. Please wait a moment and try again."
                })
                self._emit(on_event, EventType.DONE, {})
                return
            except Exception as exc:
                logger.exception("Anthropic API call failed")
                self._emit(on_event, EventType.ERROR, {"message": str(exc)})
                self._emit(on_event, EventType.DONE, {})
                return

            # ----------------------------------------------------------------
            # Process response content blocks
            # ----------------------------------------------------------------
            assistant_content: list[dict[str, Any]] = []
            tool_calls: list[dict[str, Any]] = []
            full_text = ""

            for block in response.content:
                if block.type == "text":
                    full_text += block.text
                    self._emit(on_event, EventType.TEXT_DELTA, {"text": block.text})
                    assistant_content.append({"type": "text", "text": block.text})

                elif block.type == "tool_use":
                    tool_calls.append(block)
                    self._emit(on_event, EventType.TOOL_CALL, {
                        "tool_name": block.name,
                        "tool_input": block.input,
                        "tool_use_id": block.id,
                    })
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            if full_text:
                self._emit(on_event, EventType.TEXT_DONE, {"text": full_text})

            # Append assistant turn to history
            messages.append({"role": "assistant", "content": assistant_content})

            # ----------------------------------------------------------------
            # If no tool calls, we're done
            # ----------------------------------------------------------------
            if not tool_calls or response.stop_reason != "tool_use":
                with self._lock:
                    self.conversation_history = messages
                break

            # ----------------------------------------------------------------
            # Execute tool calls and build tool_result blocks
            # ----------------------------------------------------------------
            tool_results: list[dict[str, Any]] = []
            for tc in tool_calls:
                result = self.mcp_server.execute_tool(tc.name, tc.input)
                result_text = result.get("message", str(result))
                self._emit(on_event, EventType.TOOL_RESULT, {
                    "tool_name": tc.name,
                    "tool_use_id": tc.id,
                    "result": result,
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result_text,
                })

            # Append tool results as a user turn and loop
            messages.append({"role": "user", "content": tool_results})

        self._emit(on_event, EventType.DONE, {})

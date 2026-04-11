"""
ai/claude_client.py
Anthropic Claude API client with MCP tool-use support.
Handles multi-turn conversation, tool call loops, and streams events
back via a pluggable emitter callback.

The emitter is decoupled from any specific UI framework -- the web layer
(or any other consumer) wires it up via set_emitter().
"""

import json
import logging
import threading
import uuid
from typing import Any, Callable

from ai.error_classifier import enrich_error, should_auto_undo, parse_script_error
from ai.rate_limiter import RateLimiter
from ai.system_prompt import build_system_prompt

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
# Event types emitted to the callback / emitter
# ---------------------------------------------------------------------------
class EventType:
    TEXT_DELTA   = "text_delta"       # partial text from Claude
    TEXT_DONE    = "text_done"        # full assistant text block finished
    TOOL_CALL    = "tool_call"        # Claude is calling a tool
    TOOL_RESULT  = "tool_result"      # result returned to Claude
    ERROR        = "error"            # something went wrong
    DONE         = "done"             # entire turn finished
    USAGE        = "usage"            # token usage statistics


# ---------------------------------------------------------------------------
# Geometry-modifying tools that trigger an automatic screenshot
# ---------------------------------------------------------------------------
GEOMETRY_TOOLS: set[str] = {
    "create_cylinder",
    "create_box",
    "create_sphere",
    "extrude",
    "revolve",
    "add_fillet",
    "add_chamfer",
    "mirror_body",
    "add_sketch_line",
    "add_sketch_circle",
    "add_sketch_rectangle",
    "add_sketch_arc",
}


class ClaudeClient:
    """
    Wraps the Anthropic Messages API with tool-use (MCP) support.

    Usage:
        client = ClaudeClient(settings, mcp_server)
        client.set_emitter(my_callback)       # optional default emitter
        client.send_message("Create a 5cm radius cylinder")

    The emitter callback receives (event_type: str, payload: dict).
    All network I/O runs on a background thread so the caller stays responsive.
    """

    # Toggleable feature flag for auto-screenshots after geometry tools
    auto_screenshot: bool = True

    def __init__(self, settings, mcp_server):
        self.settings = settings
        self.mcp_server = mcp_server
        self.conversation_history: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._emitter: Callable[[str, dict], None] | None = None
        self._conversation_id: str = str(uuid.uuid4())
        self._system_prompt: str = build_system_prompt(
            user_additions=self.settings.system_prompt
        )

        # -- Token usage tracking --
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.turn_count: int = 0

        # -- Rate limiter --
        try:
            rpm = int(self.settings.get("max_requests_per_minute", 10))
        except (TypeError, ValueError):
            rpm = 10
        self.rate_limiter = RateLimiter(max_requests_per_minute=rpm)

    # ------------------------------------------------------------------
    # Emitter management
    # ------------------------------------------------------------------

    def set_emitter(self, callback: Callable[[str, dict], None] | None) -> None:
        """
        Set the default emitter callback.

        The web events module calls this to wire Socket.IO emission.
        The callback signature is (event_type: str, payload: dict) -> None.
        """
        self._emitter = callback

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

        If *on_event* is provided it is used for this turn; otherwise the
        default emitter set via set_emitter() is used.
        """
        callback = on_event or self._emitter
        thread = threading.Thread(
            target=self._run_turn,
            args=(user_text, callback),
            daemon=True,
        )
        thread.start()

    def clear_history(self) -> None:
        """Reset the conversation history and token counters."""
        with self._lock:
            self.conversation_history.clear()
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.turn_count = 0

    # ------------------------------------------------------------------
    # Conversation management
    # ------------------------------------------------------------------

    def new_conversation(self) -> str:
        """
        Start a fresh conversation -- generates a new ID and clears history.

        Returns:
            The new conversation ID.
        """
        with self._lock:
            self._conversation_id = str(uuid.uuid4())
            self.conversation_history.clear()
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.turn_count = 0
        return self._conversation_id

    def get_conversation_id(self) -> str:
        """Return the current conversation ID."""
        return self._conversation_id

    def get_messages(self) -> list[dict[str, Any]]:
        """Return a copy of the current conversation history."""
        with self._lock:
            return list(self.conversation_history)

    def set_conversation(self, conversation_id: str, messages: list[dict[str, Any]]) -> None:
        """
        Restore a previously saved conversation.

        Parameters:
            conversation_id: The UUID of the conversation to restore.
            messages:        The full message list.
        """
        with self._lock:
            self._conversation_id = conversation_id
            self.conversation_history = list(messages)

    def update_config(self, api_key: str | None = None, model: str | None = None,
                      max_tokens: int | None = None, system_prompt: str | None = None,
                      max_requests_per_minute: int | None = None) -> None:
        """
        Update configuration on the underlying settings object.
        Only non-None values are written.  When system_prompt changes the
        full prompt is rebuilt to incorporate the skill document.
        """
        updates: dict[str, Any] = {}
        if api_key is not None:
            updates["anthropic_api_key"] = api_key
        if model is not None:
            updates["model"] = model
        if max_tokens is not None:
            updates["max_tokens"] = max_tokens
        if system_prompt is not None:
            updates["system_prompt"] = system_prompt
        if max_requests_per_minute is not None:
            updates["max_requests_per_minute"] = max_requests_per_minute
        if updates:
            self.settings.update(updates)

        # Rebuild the system prompt whenever it may have changed
        if system_prompt is not None:
            self._system_prompt = build_system_prompt(
                user_additions=self.settings.system_prompt
            )

        # Propagate rate-limit changes to the limiter
        if max_requests_per_minute is not None:
            self.rate_limiter.update_limit(max_requests_per_minute)

    # ------------------------------------------------------------------
    # Usage statistics
    # ------------------------------------------------------------------

    def get_usage_stats(self) -> dict[str, Any]:
        """Return accumulated token-usage statistics."""
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "turn_count": self.turn_count,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit(self, on_event, event_type: str, payload: dict) -> None:
        if on_event:
            try:
                on_event(event_type, payload)
            except Exception as exc:
                logger.warning("on_event callback raised: %s", exc)

    def _track_usage(self, response, on_event) -> None:
        """Accumulate token counts from a response and emit a USAGE event."""
        input_tokens = getattr(response.usage, "input_tokens", 0)
        output_tokens = getattr(response.usage, "output_tokens", 0)
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.turn_count += 1

        self._emit(on_event, EventType.USAGE, {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "turn_count": self.turn_count,
        })

    def _maybe_auto_screenshot(
        self,
        tool_name: str,
        raw_result: dict,
        messages: list[dict[str, Any]],
        on_event,
    ) -> None:
        """
        If auto_screenshot is enabled and *tool_name* is a geometry tool
        whose result indicates success, take a screenshot and inject it
        into the conversation as a user message so Claude sees it on the
        next loop iteration.
        """
        if not self.auto_screenshot:
            return
        if tool_name not in GEOMETRY_TOOLS:
            return
        # Only auto-screenshot when the tool succeeded
        if isinstance(raw_result, dict) and not raw_result.get("success", True):
            return

        try:
            screenshot = self.mcp_server.execute_tool("take_screenshot", {})
        except Exception as exc:
            logger.warning("Auto-screenshot failed: %s", exc)
            return

        if not isinstance(screenshot, dict) or not screenshot.get("image_base64"):
            return

        base64_data = screenshot["image_base64"]

        # Emit events so the UI can display the auto-screenshot
        self._emit(on_event, EventType.TOOL_CALL, {
            "tool_name": "take_screenshot",
            "arguments": {},
            "auto": True,
        })
        self._emit(on_event, EventType.TOOL_RESULT, {
            "tool_name": "take_screenshot",
            "result": screenshot,
            "auto": True,
        })

        # Append as a user message with an informational image so Claude
        # sees the viewport on the next iteration.  This avoids injecting
        # a fake tool_result block (the API expects exactly one per
        # tool_use).
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"[Auto-screenshot after {tool_name}]",
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64_data,
                    },
                },
            ],
        })

    # ------------------------------------------------------------------
    # Internal turn execution
    # ------------------------------------------------------------------

    def _run_turn(
        self,
        user_text: str,
        on_event: Callable[[str, dict], None] | None,
    ) -> None:
        """Full agentic loop: send -> handle tool calls -> send results -> repeat."""

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

        # Agentic loop -- keep going while Claude wants to call tools
        while True:
            # ---- Rate limiting ----
            if not self.rate_limiter.acquire(timeout=60.0):
                self._emit(on_event, EventType.ERROR, {
                    "message": (
                        "Rate limit reached -- could not acquire a request "
                        "slot within 60 s.  Please wait and try again."
                    ),
                })
                break

            # ---- API call (streaming with fallback) ----
            try:
                response = self._call_api_streaming(
                    client, messages, on_event,
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

            # ---- Token usage tracking ----
            self._track_usage(response, on_event)

            # ----------------------------------------------------------------
            # Process response content blocks
            # ----------------------------------------------------------------
            assistant_content: list[dict[str, Any]] = []
            tool_calls: list[dict[str, Any]] = []
            full_text = ""

            for block in response.content:
                if block.type == "text":
                    full_text += block.text
                    # Text deltas were already streamed in _call_api_streaming;
                    # only emit TEXT_DONE here for the consolidated block.
                    assistant_content.append({"type": "text", "text": block.text})

                elif block.type == "tool_use":
                    tool_calls.append(block)
                    self._emit(on_event, EventType.TOOL_CALL, {
                        "tool_name": block.name,
                        "arguments": block.input,
                        "tool_use_id": block.id,
                    })
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            if full_text:
                self._emit(on_event, EventType.TEXT_DONE, {"full_text": full_text})

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
            raw_results: list[tuple[str, dict]] = []  # (tool_name, raw_result)

            for tc in tool_calls:
                # -- Pre-state snapshot for geometry tools --
                pre_state = None
                if tc.name in GEOMETRY_TOOLS:
                    try:
                        pre_state = self.mcp_server.execute_tool('get_body_list', {})
                    except Exception:
                        pass

                # -- Execute the tool --
                result = self.mcp_server.execute_tool(tc.name, tc.input)

                # -- Post-execution: enrich errors or add delta --
                if isinstance(result, dict) and not result.get('success', True):
                    # --- Error enrichment ---
                    error_msg = result.get('error', '') or result.get('message', '')
                    result = enrich_error(tc.name, error_msg, result)

                    # Auto-undo for geometry errors
                    if should_auto_undo(result.get('error_type', ''), tc.name):
                        try:
                            undo_result = self.mcp_server.execute_tool('undo', {})
                            result['error_details']['auto_recovered'] = True
                            result['error_details']['recovery_action'] = 'undo'
                            self._emit(on_event, EventType.TOOL_CALL, {
                                "tool_name": "undo",
                                "arguments": {},
                                "tool_use_id": f"auto_undo_{tc.id}",
                                "auto": True,
                            })
                            self._emit(on_event, EventType.TOOL_RESULT, {
                                "tool_name": "undo",
                                "result": undo_result,
                                "tool_use_id": f"auto_undo_{tc.id}",
                                "auto": True,
                            })
                        except Exception as e:
                            logger.warning("Auto-undo failed: %s", e)
                            result['error_details']['auto_recovered'] = False

                    # Parse script errors for better diagnostics
                    if tc.name == 'execute_script' and result.get('stderr'):
                        script_error_info = parse_script_error(result['stderr'])
                        result['error_details']['script_error'] = script_error_info

                    # Add design state context to error results
                    try:
                        state = self.mcp_server.execute_tool('get_body_list', {})
                        result['design_state'] = {
                            'body_count': state.get('count', 0),
                        }
                        timeline = self.mcp_server.execute_tool('get_timeline', {})
                        result['design_state']['timeline_length'] = len(
                            timeline.get('timeline', [])
                        )
                    except Exception:
                        pass  # Don't let state query failure mask the original error

                elif isinstance(result, dict) and result.get('success') and pre_state and tc.name in GEOMETRY_TOOLS:
                    # --- Verification delta for successful geometry ops ---
                    try:
                        post_state = self.mcp_server.execute_tool('get_body_list', {})
                        result['delta'] = {
                            'bodies_before': pre_state.get('count', 0),
                            'bodies_after': post_state.get('count', 0),
                            'bodies_added': post_state.get('count', 0) - pre_state.get('count', 0),
                        }
                    except Exception:
                        pass

                raw_results.append((tc.name, result))

                self._emit(on_event, EventType.TOOL_RESULT, {
                    "tool_name": tc.name,
                    "tool_use_id": tc.id,
                    "result": result,
                })

                # Build the content for the tool_result message.
                # For take_screenshot, include the image as a multimodal
                # content block so Claude can "see" the viewport.
                if (
                    tc.name == "take_screenshot"
                    and isinstance(result, dict)
                    and result.get("success")
                    and result.get("image_base64")
                ):
                    tool_result_content = [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": result["image_base64"],
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"Screenshot captured "
                                f"({result.get('width', '?')}x{result.get('height', '?')} pixels)"
                            ),
                        },
                    ]
                else:
                    tool_result_content = json.dumps(result)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": tool_result_content,
                })

            # Append tool results as a user turn
            messages.append({"role": "user", "content": tool_results})

            # ---- Auto-screenshot after geometry tools ----
            # Check each executed tool; if any was geometry-modifying and
            # succeeded, take one auto-screenshot (deduplicated -- only the
            # last geometry tool triggers it to avoid multiple screenshots).
            last_geometry_tool = None
            last_geometry_result = None
            for tool_name, raw_result in raw_results:
                if tool_name in GEOMETRY_TOOLS:
                    last_geometry_tool = tool_name
                    last_geometry_result = raw_result

            if last_geometry_tool is not None:
                self._maybe_auto_screenshot(
                    last_geometry_tool,
                    last_geometry_result,
                    messages,
                    on_event,
                )

        self._emit(on_event, EventType.DONE, {})

    # ------------------------------------------------------------------
    # Streaming API call with fallback
    # ------------------------------------------------------------------

    def _call_api_streaming(self, client, messages, on_event):
        """
        Call the Anthropic Messages API using the streaming context manager.
        Text deltas are emitted in real-time via *on_event*.
        Falls back to the synchronous ``messages.create()`` if streaming
        is unavailable.

        Returns the final ``Message`` object in both paths.
        """
        try:
            with client.messages.stream(
                model=self.settings.model,
                max_tokens=self.settings.max_tokens,
                system=self._system_prompt,
                tools=self.mcp_server.tool_definitions,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    self._emit(on_event, EventType.TEXT_DELTA, {"text": text})

                response = stream.get_final_message()
            return response

        except (AttributeError, TypeError):
            # Older SDK without messages.stream -- fall back to sync call
            logger.info(
                "Streaming unavailable (SDK too old?); falling back to "
                "synchronous messages.create()"
            )
            response = client.messages.create(
                model=self.settings.model,
                max_tokens=self.settings.max_tokens,
                system=self._system_prompt,
                tools=self.mcp_server.tool_definitions,
                messages=messages,
            )
            # Emit text blocks that weren't streamed
            for block in response.content:
                if block.type == "text":
                    self._emit(on_event, EventType.TEXT_DELTA, {"text": block.text})
            return response

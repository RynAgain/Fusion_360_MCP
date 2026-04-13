"""
ai/claude_client.py
LLM API client with MCP tool-use support.
Handles multi-turn conversation, tool call loops, and streams events
back via a pluggable emitter callback.

The emitter is decoupled from any specific UI framework -- the web layer
(or any other consumer) wires it up via set_emitter().

The actual LLM backend is selected via the provider abstraction layer
(see ai/providers/).  Anthropic and Ollama are supported out of the box.
"""

import json
import logging
import re
import threading
import uuid
from typing import Any, Callable

from ai.checkpoint_manager import CheckpointManager
from ai.context_manager import ContextManager
from ai.design_state_tracker import DesignStateTracker
from ai.error_classifier import enrich_error, should_auto_undo, parse_script_error
from ai.modes import ModeManager
from ai.providers.provider_manager import ProviderManager
from ai.rate_limiter import RateLimiter
from ai.repetition_detector import RepetitionDetector
from ai.system_prompt import build_system_prompt
from ai.task_manager import TaskManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try to import the Anthropic SDK (for provider-specific error handling)
# ---------------------------------------------------------------------------
try:
    import anthropic as _anthropic_module
    _ANTHROPIC_ERRORS_AVAILABLE = True
except ImportError:
    _ANTHROPIC_ERRORS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Event types emitted to the callback / emitter
# ---------------------------------------------------------------------------
class EventType:
    TEXT_DELTA   = "text_delta"       # partial text from LLM
    TEXT_DONE    = "text_done"        # full assistant text block finished
    TOOL_CALL    = "tool_call"        # LLM is calling a tool
    TOOL_RESULT  = "tool_result"      # result returned to LLM
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
    "delete_body",
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

# Geometry tools that trigger pre/post delta capture via DesignStateTracker.
# This is a superset of GEOMETRY_TOOLS -- it includes sketch primitives,
# script execution, and additional modelling operations.
_DELTA_GEOMETRY_TOOLS: set[str] = GEOMETRY_TOOLS | {
    "fillet",
    "chamfer",
    "shell",
    "combine",
    "execute_script",
    "create_sketch",
    "add_sketch_point",
    "add_sketch_polygon",
    "add_sketch_spline",
    "add_sketch_slot",
    "add_sketch_mirror",
}

# Tools where a "cut" or subtractive operation may silently fail.
# Delta verification should check volume/face_count in addition to body count.
_CUT_LIKE_TOOLS: set[str] = {"extrude", "revolve"}

# Geometry tools that modify bodies and warrant mandatory post-op delta checks.
_BODY_MODIFYING_TOOLS: set[str] = {
    "extrude", "revolve", "add_fillet", "add_chamfer", "fillet", "chamfer",
    "shell", "combine", "delete_body",
}

# Fillet/chamfer tools where face_count should increase on success.
_FILLET_CHAMFER_TOOLS: set[str] = {"add_fillet", "add_chamfer", "fillet", "chamfer"}


# ---------------------------------------------------------------------------
# Action intent patterns -- phrases indicating the model wants to act but
# did not include a tool call.  Used by the auto-continue mechanism.
# ---------------------------------------------------------------------------
_ACTION_INTENT_PATTERNS = [
    re.compile(
        r"\bI('ll| will| am going to|'m going to)\b.*\b(create|make|add|draw|sketch|extrude|revolve|fillet|chamfer|execute|run|call|use|apply|export|save|delete|mirror|undo|redo)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(Let me|Let's|I'll proceed|I'll start|I'll begin|I'll now|Now I'll|I'm going to)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(executing|running|calling|creating|making|building|generating|constructing|designing)\b.*\b(script|tool|function|command)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bPlease wait\b", re.IGNORECASE),
    re.compile(
        r"\bI need to (create|make|add|execute|run)\b", re.IGNORECASE
    ),
]

# Maximum number of auto-continue nudges per user message
_MAX_AUTO_CONTINUES = 2


class ClaudeClient:
    """
    Wraps any supported LLM API with tool-use (MCP) support.

    Usage:
        client = ClaudeClient(settings, mcp_server)
        client.set_emitter(my_callback)       # optional default emitter
        client.send_message("Create a 5cm radius cylinder")

    The emitter callback receives (event_type: str, payload: dict).
    All network I/O runs on a background thread so the caller stays responsive.
    """

    # Toggleable feature flag for auto-screenshots after geometry tools
    auto_screenshot: bool = True

    # Maximum number of auto-screenshots allowed per _run_turn invocation.
    # User-requested take_screenshot calls do NOT count against this budget.
    MAX_SCREENSHOTS_PER_TURN: int = 3

    def __init__(self, settings, mcp_server):
        self.settings = settings
        self.mcp_server = mcp_server
        self.conversation_history: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._emitter: Callable[[str, dict], None] | None = None
        self._conversation_id: str = str(uuid.uuid4())
        self._system_prompt: str = build_system_prompt(
            user_additions=self.settings.system_prompt,
            mode=None,  # no mode active yet
        )

        # -- Token usage tracking --
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.turn_count: int = 0

        # -- Screenshot budget (reset each _run_turn) --
        self._screenshot_count: int = 0

        # -- Rate limiter --
        try:
            rpm = int(self.settings.get("max_requests_per_minute", 10))
        except (TypeError, ValueError):
            rpm = 10
        self.rate_limiter = RateLimiter(max_requests_per_minute=rpm)

        # -- Context manager (conversation condensation) --
        self.context_manager = ContextManager(model=self.settings.model)

        # -- Repetition detector --
        self.repetition_detector = RepetitionDetector()

        # -- Mode manager (CAD mode system) --
        self.mode_manager = ModeManager()

        # -- Task manager (design plan tracking) --
        self.task_manager = TaskManager()

        # -- Checkpoint manager (design restore points) --
        self.checkpoint_manager = CheckpointManager()

        # -- Design state tracker (persistent CAD state) --
        self._design_state = DesignStateTracker()

        # -- Provider manager (LLM backend abstraction) --
        self.provider_manager = ProviderManager()

        # Configure Anthropic provider
        if settings.api_key:
            self.provider_manager.configure_provider(
                "anthropic", api_key=settings.api_key
            )

        # Configure Ollama provider
        ollama_url = getattr(settings, "ollama_base_url", "http://localhost:11434")
        self.provider_manager.configure_provider("ollama", base_url=ollama_url)

        # Set active provider from settings
        provider_type = getattr(settings, "provider", "anthropic")
        try:
            self.provider_manager.switch(provider_type)
        except ValueError:
            logger.warning(
                "Unknown provider '%s' in settings; defaulting to anthropic",
                provider_type,
            )
            self.provider_manager.switch("anthropic")

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
        Send a user message to the LLM in a background thread.

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
        self.context_manager.reset()
        self.repetition_detector.reset()
        self.task_manager.clear()
        self.checkpoint_manager.clear()
        self._design_state.reset()

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
        self.context_manager.reset()
        self.repetition_detector.reset()
        self.task_manager.clear()
        self.checkpoint_manager.clear()
        self._design_state.reset()
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
                      max_requests_per_minute: int | None = None,
                      provider: str | None = None,
                      ollama_base_url: str | None = None) -> None:
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
        if provider is not None:
            updates["provider"] = provider
        if ollama_base_url is not None:
            updates["ollama_base_url"] = ollama_base_url
        if updates:
            self.settings.update(updates)

        # Rebuild the system prompt whenever it may have changed
        if system_prompt is not None:
            self._system_prompt = build_system_prompt(
                user_additions=self.settings.system_prompt,
                mode=self.mode_manager.active_slug,
            )

        # Propagate model changes to the context manager
        if model is not None:
            self.context_manager.update_model(self.settings.model)

        # Propagate rate-limit changes to the limiter
        if max_requests_per_minute is not None:
            self.rate_limiter.update_limit(max_requests_per_minute)

        # Propagate provider changes
        if provider is not None:
            try:
                self.provider_manager.switch(provider)
            except ValueError as exc:
                logger.warning("Provider switch failed: %s", exc)
        if api_key is not None:
            self.provider_manager.configure_provider("anthropic", api_key=api_key)
        if ollama_base_url is not None:
            self.provider_manager.configure_provider("ollama", base_url=ollama_base_url)

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def switch_provider(self, provider_type: str) -> dict:
        """Switch the active LLM provider and return info about it."""
        provider = self.provider_manager.switch(provider_type)
        # Persist to settings
        self.settings.update({"provider": provider_type})
        return {
            "type": provider_type,
            "name": provider.name,
            "is_available": provider.is_available(),
        }

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

    def get_design_state(self) -> dict[str, Any]:
        """Return the current tracked design state as a dict."""
        return self._design_state.to_dict()

    # ------------------------------------------------------------------
    # Mode management
    # ------------------------------------------------------------------

    def switch_mode(self, mode_slug: str) -> dict:
        """Switch the active CAD mode and return its definition."""
        mode = self.mode_manager.switch_mode(mode_slug)
        # Rebuild system prompt to include mode-specific rules
        self._system_prompt = build_system_prompt(
            user_additions=self.settings.system_prompt,
            mode=mode_slug,
        )
        return mode.to_dict()

    # ------------------------------------------------------------------
    # Design plan management
    # ------------------------------------------------------------------

    def create_design_plan(self, title: str, steps: list[str]) -> dict:
        """Create a new design plan and return its state."""
        self.task_manager.create_plan(title, steps)
        return self.task_manager.to_dict()

    def update_task(self, index: int, status: str, result: str = "") -> dict:
        """Update a task step status and return the full plan state."""
        if status == "completed":
            self.task_manager.complete_step(index, result)
        elif status == "failed":
            self.task_manager.fail_step(index, result)
        elif status == "in_progress":
            self.task_manager.start_step(index)
        elif status == "skipped":
            self.task_manager.skip_step(index)
        return self.task_manager.to_dict()

    # ------------------------------------------------------------------
    # Checkpoint management
    # ------------------------------------------------------------------

    def save_checkpoint(self, name: str, description: str = "") -> dict:
        """Save a design checkpoint at the current state."""
        cp = self.checkpoint_manager.save(name, self.mcp_server, len(self.conversation_history), description)
        return cp.to_dict()

    def restore_checkpoint(self, name: str) -> dict:
        """Restore to a previously saved design checkpoint."""
        result = self.checkpoint_manager.restore(name, self.mcp_server, self.conversation_history)
        if result.get('success'):
            # Truncate conversation to checkpoint's message index
            new_count = result['new_message_count']
            self.conversation_history = self.conversation_history[:new_count]
        return result

    def list_checkpoints(self) -> list[dict]:
        """List all saved design checkpoints."""
        return self.checkpoint_manager.list_all()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit(self, on_event, event_type: str, payload: dict) -> None:
        if on_event:
            try:
                on_event(event_type, payload)
            except Exception as exc:
                logger.warning("on_event callback raised: %s", exc)

    @staticmethod
    def _has_action_intent(text: str) -> bool:
        """Check if *text* expresses intent to act without actually acting.

        Returns True when the assistant's text contains phrases like
        "I'll create...", "Let me execute...", etc. -- indicating it
        planned an action but did not include a tool call.
        """
        if not text or len(text) < 20:
            return False
        for pattern in _ACTION_INTENT_PATTERNS:
            if pattern.search(text):
                return True
        return False

    def _track_usage(self, response, on_event) -> None:
        """Accumulate token counts from an LLMResponse and emit a USAGE event."""
        input_tokens = response.usage.get("input_tokens", 0)
        output_tokens = response.usage.get("output_tokens", 0)
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
        If auto_screenshot is enabled and *tool_name* is a body-modifying
        tool whose result indicates success **and** the design state delta
        shows actual changes, take a reduced-resolution screenshot and
        inject it into the conversation as a user message so the LLM sees
        it on the next loop iteration.

        Only body-modifying tools trigger auto-screenshots (not sketch-only
        tools).  A per-turn budget (MAX_SCREENSHOTS_PER_TURN) prevents
        excessive screenshot capture.  User-requested ``take_screenshot``
        calls via the agent are NOT counted against this budget.
        """
        if not self.auto_screenshot:
            return
        # -- Selective: only body-modifying tools trigger auto-screenshots --
        if tool_name not in _BODY_MODIFYING_TOOLS:
            return
        # Only auto-screenshot when the tool succeeded
        if isinstance(raw_result, dict) and not raw_result.get("success", True):
            return

        # -- Check design-state delta: skip if no geometry changed --
        if isinstance(raw_result, dict):
            delta = raw_result.get("delta", {})
            if delta:
                bodies_added = delta.get("bodies_added", 0)
                bodies_removed = delta.get("bodies_removed", [])
                bodies_modified = delta.get("bodies_modified", [])
                no_changes = (
                    bodies_added == 0
                    and len(bodies_removed) == 0
                    and len(bodies_modified) == 0
                )
                if no_changes:
                    logger.debug(
                        "Skipping auto-screenshot for '%s': delta shows no changes",
                        tool_name,
                    )
                    return

        # -- Budget check --
        if self._screenshot_count >= self.MAX_SCREENSHOTS_PER_TURN:
            logger.debug(
                "Screenshot budget exceeded (%d/%d), skipping auto-screenshot",
                self._screenshot_count,
                self.MAX_SCREENSHOTS_PER_TURN,
            )
            return

        try:
            # Use reduced resolution for intermediate auto-screenshots
            screenshot = self.mcp_server.execute_tool(
                "take_screenshot", {"width": 960, "height": 540},
            )
        except Exception as exc:
            logger.warning("Auto-screenshot failed: %s", exc)
            return

        if not isinstance(screenshot, dict) or not screenshot.get("image_base64"):
            return

        # Successfully captured -- increment budget counter
        self._screenshot_count += 1

        base64_data = screenshot["image_base64"]

        # Emit events so the UI can display the auto-screenshot
        self._emit(on_event, EventType.TOOL_CALL, {
            "tool_name": "take_screenshot",
            "arguments": {"width": 960, "height": 540},
            "auto": True,
        })
        self._emit(on_event, EventType.TOOL_RESULT, {
            "tool_name": "take_screenshot",
            "result": screenshot,
            "auto": True,
        })

        # Append as a user message with an informational image so the LLM
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

        # Reset per-turn screenshot budget
        self._screenshot_count = 0

        provider = self.provider_manager.active

        if not provider.is_available():
            ptype = self.provider_manager.active_type
            if ptype == "anthropic":
                self._emit(on_event, EventType.ERROR, {
                    "message": (
                        "Anthropic provider is not available. "
                        "Ensure the anthropic package is installed and an API key is configured."
                    ),
                })
            elif ptype == "ollama":
                self._emit(on_event, EventType.ERROR, {
                    "message": (
                        "Ollama provider is not available. "
                        "Ensure Ollama is running (ollama serve) and reachable."
                    ),
                })
            else:
                self._emit(on_event, EventType.ERROR, {
                    "message": f"LLM provider '{ptype}' is not available.",
                })
            self._emit(on_event, EventType.DONE, {})
            return

        # Append user message to history
        with self._lock:
            self.conversation_history.append({"role": "user", "content": user_text})
            messages = list(self.conversation_history)

        # Agentic loop -- keep going while the LLM wants to call tools
        auto_continue_count = 0
        while True:
            # ---- Context condensation ----
            if self.context_manager.should_condense(messages, self._system_prompt):
                logger.info("Context threshold reached -- condensing conversation")
                self._emit(on_event, "condensing", {
                    "message": "Condensing conversation history..."
                })
                messages = self.context_manager.condense(
                    messages, self,
                    design_state_summary=self._design_state.to_summary_string(),
                )
                with self._lock:
                    self.conversation_history = list(messages)
                self._emit(on_event, "condensed", {
                    "message": "Conversation condensed",
                    "stats": self.context_manager.get_stats(),
                })

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
                    messages, on_event,
                )
            except Exception as exc:
                # Handle provider-specific error types
                error_msg = str(exc)
                if _ANTHROPIC_ERRORS_AVAILABLE:
                    if isinstance(exc, _anthropic_module.AuthenticationError):
                        error_msg = "Invalid Anthropic API key. Please check your settings."
                    elif isinstance(exc, _anthropic_module.RateLimitError):
                        error_msg = "Anthropic rate limit hit. Please wait a moment and try again."
                logger.exception("LLM API call failed")
                self._emit(on_event, EventType.ERROR, {"message": error_msg})
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
                if block["type"] == "text":
                    full_text += block["text"]
                    # Text deltas were already streamed in _call_api_streaming;
                    # only emit TEXT_DONE here for the consolidated block.
                    assistant_content.append({"type": "text", "text": block["text"]})

                elif block["type"] == "tool_use":
                    tool_calls.append(block)
                    self._emit(on_event, EventType.TOOL_CALL, {
                        "tool_name": block["name"],
                        "arguments": block["input"],
                        "tool_use_id": block["id"],
                    })
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block["id"],
                        "name": block["name"],
                        "input": block["input"],
                    })

            if full_text:
                self._emit(on_event, EventType.TEXT_DONE, {"full_text": full_text})

            # Append assistant turn to history
            messages.append({"role": "assistant", "content": assistant_content})

            # ----------------------------------------------------------------
            # If no tool calls, check for intent-without-action
            # ----------------------------------------------------------------
            if not tool_calls or response.stop_reason != "tool_use":
                # Check if the assistant expressed intent to act but didn't
                # call a tool -- nudge it to follow through.
                if (
                    self._has_action_intent(full_text)
                    and auto_continue_count < _MAX_AUTO_CONTINUES
                ):
                    auto_continue_count += 1
                    nudge_msg = {
                        "role": "user",
                        "content": (
                            "[SYSTEM] You described an action but did not "
                            "execute it. Call the appropriate tool now. Do "
                            "not describe what you will do -- just do it."
                        ),
                    }
                    messages.append(nudge_msg)
                    self._emit(on_event, EventType.TEXT_DELTA, {
                        "text": "\n[Continuing autonomously...]\n",
                    })
                    logger.info(
                        "Auto-continue triggered (attempt %d/%d)",
                        auto_continue_count,
                        _MAX_AUTO_CONTINUES,
                    )
                    continue  # loop back for another API call

                with self._lock:
                    self.conversation_history = messages
                break

            # ----------------------------------------------------------------
            # Execute tool calls and build tool_result blocks
            # ----------------------------------------------------------------
            tool_results: list[dict[str, Any]] = []
            raw_results: list[tuple[str, dict]] = []  # (tool_name, raw_result)

            for tc in tool_calls:
                tc_name = tc["name"]
                tc_input = tc["input"]
                tc_id = tc["id"]

                # -- Pre-state snapshot for delta geometry tools --
                pre_state_snapshot = None
                if tc_name in _DELTA_GEOMETRY_TOOLS:
                    try:
                        self._design_state.update(self.mcp_server)
                        pre_state_snapshot = self._design_state.to_dict()
                    except Exception:
                        pass

                # -- Pre-cut validation --
                # Before executing cut operations, check sketch profiles
                # and body existence to warn about likely failures.
                if (
                    tc_name in _CUT_LIKE_TOOLS
                    and str(tc_input.get('operation', '')).lower() == 'cut'
                ):
                    try:
                        # Check sketch has profiles
                        sketch_name = tc_input.get('sketch_name', '')
                        if sketch_name:
                            sketch_info = self.mcp_server.execute_tool(
                                'get_sketch_info', {'sketch_name': sketch_name}
                            )
                            if isinstance(sketch_info, dict):
                                profiles = sketch_info.get('profiles', [])
                                if len(profiles) == 0:
                                    logger.warning(
                                        "Pre-cut validation: sketch '%s' has 0 profiles",
                                        sketch_name,
                                    )
                                    self._emit(on_event, "warning", {
                                        "message": (
                                            f"[PRE-CUT WARNING] Sketch '{sketch_name}' has no "
                                            f"profiles. The cut will fail. Ensure the sketch "
                                            f"has closed geometry."
                                        ),
                                    })
                        # Check bodies exist to cut
                        body_list = self.mcp_server.execute_tool('get_body_list', {})
                        if isinstance(body_list, dict) and body_list.get('count', 0) == 0:
                            logger.warning("Pre-cut validation: no bodies exist to cut")
                            self._emit(on_event, "warning", {
                                "message": (
                                    "[PRE-CUT WARNING] No bodies exist to cut. "
                                    "Create geometry first."
                                ),
                            })
                    except Exception as exc:
                        logger.debug("Pre-cut validation query failed (non-fatal): %s", exc)

                # -- Repetition detection --
                rep_check = self.repetition_detector.record(tc_name, tc_input)
                if rep_check["repeated"]:
                    alternatives = self.repetition_detector.get_alternatives(
                        tc_name, tc_input,
                    )
                    warning_msg = (
                        f"[REPETITION WARNING] Tool '{tc_name}' called "
                        f"{rep_check['count']} times with "
                        f"{'identical' if rep_check['type'] == 'identical' else 'similar'} "
                        f"args. Suggested alternatives: {alternatives}"
                    )
                    rep_check["suggested_alternatives"] = alternatives
                    logger.warning("Repetition detected: %s", warning_msg)
                    self._emit(on_event, "warning", {
                        "message": warning_msg,
                    })

                # -- Execute the tool --
                result = self.mcp_server.execute_tool(tc_name, tc_input)

                # -- Inject repetition warning into result --
                if rep_check["repeated"] and isinstance(result, dict):
                    result["repetition_warning"] = warning_msg
                    if rep_check.get("suggested_alternatives"):
                        result["suggested_alternatives"] = rep_check["suggested_alternatives"]
                    # Force-stop on identical repetition (3+ identical calls)
                    if rep_check.get("type") == "identical":
                        result["_force_stop"] = True

                # -- Post-execution: enrich errors or add delta --
                if isinstance(result, dict) and not result.get('success', True):
                    # --- Error enrichment ---
                    error_msg = result.get('error', '') or result.get('message', '')
                    result = enrich_error(tc_name, error_msg, result)

                    # Auto-undo for geometry errors
                    if should_auto_undo(result.get('error_type', ''), tc_name):
                        try:
                            undo_result = self.mcp_server.execute_tool('undo', {})
                            result['error_details']['auto_recovered'] = True
                            result['error_details']['recovery_action'] = 'undo'
                            self._emit(on_event, EventType.TOOL_CALL, {
                                "tool_name": "undo",
                                "arguments": {},
                                "tool_use_id": f"auto_undo_{tc_id}",
                                "auto": True,
                            })
                            self._emit(on_event, EventType.TOOL_RESULT, {
                                "tool_name": "undo",
                                "result": undo_result,
                                "tool_use_id": f"auto_undo_{tc_id}",
                                "auto": True,
                            })
                        except Exception as e:
                            logger.warning("Auto-undo failed: %s", e)
                            result['error_details']['auto_recovered'] = False

                    # Parse script errors for better diagnostics
                    if tc_name == 'execute_script' and result.get('stderr'):
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

                    # -- Automatic diagnostic queries --
                    # Based on the failed tool, run additional queries to give
                    # the agent richer context for error recovery.
                    diagnostic_data: dict[str, Any] = {}

                    try:
                        if tc_name in ('extrude', 'revolve'):
                            sketch_name = tc_input.get('sketch_name', '')
                            if sketch_name:
                                logger.debug(
                                    "Diagnostic query: get_sketch_info for '%s'",
                                    sketch_name,
                                )
                                diag_sketch = self.mcp_server.execute_tool(
                                    'get_sketch_info',
                                    {'sketch_name': sketch_name},
                                )
                                diagnostic_data['sketch_info'] = diag_sketch
                    except Exception as diag_exc:
                        logger.debug("Diagnostic sketch query failed: %s", diag_exc)

                    try:
                        err_str = str(error_msg).lower()
                        if 'body' in err_str and 'not found' in err_str:
                            logger.debug(
                                "Diagnostic query: get_body_list (body not found)",
                            )
                            diag_bodies = self.mcp_server.execute_tool(
                                'get_body_list', {},
                            )
                            diagnostic_data['body_list'] = diag_bodies
                    except Exception as diag_exc:
                        logger.debug("Diagnostic body-list query failed: %s", diag_exc)

                    try:
                        if tc_name == 'execute_script':
                            logger.debug(
                                "Diagnostic query: get_body_list (script failure)",
                            )
                            diag_bodies = self.mcp_server.execute_tool(
                                'get_body_list', {},
                            )
                            diagnostic_data['body_list'] = diag_bodies
                    except Exception as diag_exc:
                        logger.debug("Diagnostic script-state query failed: %s", diag_exc)

                    try:
                        if tc_name in ('add_fillet', 'add_chamfer'):
                            body_name = tc_input.get('body_name', '')
                            if body_name:
                                logger.debug(
                                    "Diagnostic query: get_body_properties for '%s'",
                                    body_name,
                                )
                                diag_body = self.mcp_server.execute_tool(
                                    'get_body_properties',
                                    {'body_name': body_name},
                                )
                                diagnostic_data['body_properties'] = diag_body
                    except Exception as diag_exc:
                        logger.debug("Diagnostic body-props query failed: %s", diag_exc)

                    if diagnostic_data:
                        result['diagnostic_data'] = diagnostic_data

                elif isinstance(result, dict) and result.get('success') and pre_state_snapshot and tc_name in _DELTA_GEOMETRY_TOOLS:
                    # --- Verification delta for successful geometry ops ---
                    try:
                        self._design_state.update(self.mcp_server)
                        post_state_snapshot = self._design_state.to_dict()
                        rich_delta = self._design_state.get_delta(pre_state_snapshot)

                        # Populate the result['delta'] from the rich delta
                        pre_body_count = len(pre_state_snapshot.get('bodies', []))
                        post_body_count = len(post_state_snapshot.get('bodies', []))
                        result['delta'] = {
                            'bodies_before': pre_body_count,
                            'bodies_after': post_body_count,
                            'bodies_added': len(rich_delta.get('bodies_added', [])),
                            'bodies_removed': [b['name'] for b in rich_delta.get('bodies_removed', [])],
                            'bodies_modified': rich_delta.get('bodies_modified', []),
                            'timeline_position_change': rich_delta.get('timeline_position_change'),
                        }

                        # Enhanced verification for cut/join/intersect ops:
                        # compare volume to detect silent failures.
                        is_cut_op = (
                            tc_name in _CUT_LIKE_TOOLS
                            and str(tc_input.get('operation', '')).lower() in ('cut', 'intersect', 'join')
                        )
                        if is_cut_op:
                            op_type = str(tc_input.get('operation', '')).lower()
                            pre_bodies = pre_state_snapshot.get('bodies', [])
                            post_bodies = post_state_snapshot.get('bodies', [])
                            vol_before = sum(b.get('volume', 0) for b in pre_bodies)
                            vol_after = sum(b.get('volume', 0) for b in post_bodies)
                            face_before = sum(b.get('face_count', 0) for b in pre_bodies)
                            face_after = sum(b.get('face_count', 0) for b in post_bodies)

                            result['delta']['volume_before'] = vol_before
                            result['delta']['volume_after'] = vol_after
                            result['delta']['face_count_before'] = face_before
                            result['delta']['face_count_after'] = face_after

                            # Inject warning if cut didn't reduce volume
                            if op_type == 'cut' and vol_before > 0 and vol_after >= vol_before:
                                result['delta']['warning'] = (
                                    f"[WARNING] Cut operation may have failed: volume did not decrease "
                                    f"(before: {vol_before:.4f}, after: {vol_after:.4f}). "
                                    f"Verify the result with take_screenshot."
                                )
                                logger.warning(
                                    "Cut operation silent failure detected: vol_before=%.4f vol_after=%.4f",
                                    vol_before, vol_after,
                                )

                        # -- Mandatory post-op verification for body-modifying tools --
                        if tc_name in _BODY_MODIFYING_TOOLS:
                            delta = result.get('delta', {})
                            bodies_added = delta.get('bodies_added', 0)
                            bodies_removed = delta.get('bodies_removed', [])
                            bodies_modified = delta.get('bodies_modified', [])
                            no_changes = (
                                bodies_added == 0
                                and len(bodies_removed) == 0
                                and len(bodies_modified) == 0
                            )
                            if no_changes:
                                warning_msg = (
                                    "[POST-OP WARNING] Operation completed but no geometry "
                                    "changes detected. The operation may have silently failed. "
                                    "Use take_screenshot to verify."
                                )
                                result['delta'].setdefault('warning', '')
                                if result['delta']['warning']:
                                    result['delta']['warning'] += ' | ' + warning_msg
                                else:
                                    result['delta']['warning'] = warning_msg
                                logger.warning(
                                    "Post-op no-change detected for tool '%s'", tc_name,
                                )

                            # Fillet/chamfer specific: check face_count increase
                            if tc_name in _FILLET_CHAMFER_TOOLS:
                                face_before = sum(
                                    b.get('face_count', 0)
                                    for b in pre_state_snapshot.get('bodies', [])
                                )
                                face_after = sum(
                                    b.get('face_count', 0)
                                    for b in post_state_snapshot.get('bodies', [])
                                )
                                if face_after <= face_before:
                                    fc_warning = (
                                        "[POST-OP WARNING] Fillet/chamfer completed but face "
                                        "count did not increase. The feature may not have "
                                        "been applied."
                                    )
                                    result['delta'].setdefault('warning', '')
                                    if result['delta']['warning']:
                                        result['delta']['warning'] += ' | ' + fc_warning
                                    else:
                                        result['delta']['warning'] = fc_warning
                                    logger.warning(
                                        "Fillet/chamfer no face increase: before=%d after=%d",
                                        face_before, face_after,
                                    )

                    except Exception:
                        pass

                raw_results.append((tc_name, result))

                self._emit(on_event, EventType.TOOL_RESULT, {
                    "tool_name": tc_name,
                    "tool_use_id": tc_id,
                    "result": result,
                })

                # Build the content for the tool_result message.
                # For take_screenshot, include the image as a multimodal
                # content block so the LLM can "see" the viewport.
                if (
                    tc_name == "take_screenshot"
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
                    "tool_use_id": tc_id,
                    "content": tool_result_content,
                })

            # Append tool results as a user turn
            messages.append({"role": "user", "content": tool_results})

            # ---- Force-stop on identical repetition ----
            # If any tool result was flagged for force-stop, inject a strong
            # system message and break out of the tool loop to force the
            # model to respond with text instead of repeating the same call.
            force_stop = any(
                isinstance(raw, dict) and raw.get("_force_stop")
                for _, raw in raw_results
            )
            if force_stop:
                messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM] You are repeating the same tool call with "
                        "identical arguments. STOP and explain to the user what "
                        "is going wrong and what alternative approaches are "
                        "available. Do NOT call the same tool again."
                    ),
                })
                with self._lock:
                    self.conversation_history = messages
                break

            # ---- Auto-screenshot after body-modifying tools ----
            # Check each executed tool; if any was body-modifying and
            # succeeded, take one auto-screenshot (deduplicated -- only the
            # last body-modifying tool triggers it to avoid multiple
            # screenshots per loop iteration).
            last_body_tool = None
            last_body_result = None
            for tool_name, raw_result in raw_results:
                if tool_name in _BODY_MODIFYING_TOOLS:
                    last_body_tool = tool_name
                    last_body_result = raw_result

            if last_body_tool is not None:
                self._maybe_auto_screenshot(
                    last_body_tool,
                    last_body_result,
                    messages,
                    on_event,
                )

        self._emit(on_event, EventType.DONE, {})

    # ------------------------------------------------------------------
    # Streaming API call with fallback
    # ------------------------------------------------------------------

    def _build_effective_prompt(self) -> str:
        """Build the effective system prompt including mode and task context."""
        effective_prompt = self._system_prompt

        # Append mode-specific instructions
        mode_additions = self.mode_manager.get_mode_prompt_additions()
        if mode_additions:
            effective_prompt += "\n\n" + mode_additions

        # Append active design plan context
        task_context = self.task_manager.get_context_injection()
        if task_context:
            effective_prompt += task_context

        return effective_prompt

    def _get_filtered_tools(self) -> list[dict[str, Any]]:
        """Return tool definitions filtered to the current mode."""
        all_tools = self.mcp_server.tool_definitions
        allowed = self.mode_manager.get_allowed_tools()
        return [t for t in all_tools if t["name"] in allowed]

    def _call_api_streaming(self, messages, on_event):
        """
        Call the active LLM provider using streaming.
        Text deltas are emitted in real-time via *on_event*.
        Falls back to the synchronous path if streaming is unavailable.

        Returns the final ``LLMResponse`` object.
        """
        effective_prompt = self._build_effective_prompt()
        filtered_tools = self._get_filtered_tools()
        provider = self.provider_manager.active

        def on_text(chunk):
            self._emit(on_event, EventType.TEXT_DELTA, {"text": chunk})

        response = provider.stream_message(
            messages=messages,
            system=effective_prompt,
            tools=filtered_tools,
            max_tokens=self.settings.max_tokens,
            model=self.settings.model,
            text_callback=on_text,
        )

        return response

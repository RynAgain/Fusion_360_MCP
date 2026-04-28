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
from enum import Enum
from typing import Any, Callable

from ai.checkpoint_manager import CheckpointManager
from ai.context_bridge import ContextBridge
from ai.context_manager import ContextManager
from ai.context_window_guard import (
    AdequacyLevel,
    ContextWindowGuard,
    CONTEXT_PRESSURE_MESSAGE,
    CRITICAL_CONCISENESS_MESSAGE,
)
from ai.design_state_tracker import DesignStateTracker
from ai.error_classifier import enrich_error, should_auto_undo, parse_script_error
from ai.message_queue import MessageQueue
from ai.modes import ModeManager
from ai.progress_tracker import ProgressTracker
from ai.providers.provider_manager import ProviderManager
from ai.rate_limiter import RateLimiter
from ai.repetition_detector import RepetitionDetector, ScriptErrorTracker, RebuildLoopDetector
from ai.subtask_manager import SubtaskManager
from ai.system_prompt import build_system_prompt
from ai.task_manager import TaskManager
from ai.tool_recovery import format_diagnostic_summary, deduplicate_script_error
from ai.session_report import SessionFailureReport

# ---------------------------------------------------------------------------
# TASK-080: ClaudeClient Decomposition Plan
#
# This class is ~1530 lines with ~30 public methods. The following modules
# should be extracted in future PRs:
#
#   1. AgentLoop (the while-true tool loop in _run_turn_inner)
#   2. TurnState (per-turn conversation snapshot, version tracking)
#   3. TokenTracker (token counting, budget management)
#   4. ScreenshotBudget (screenshot frequency, cooldown logic)
#
# ClaudeClient should become a thin coordinator that delegates to these.
# See FEATURES.md TASK-080 for full details.
# ---------------------------------------------------------------------------

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
class EventType(str, Enum):
    """Event types emitted during agent operations."""
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

# TASK-224: Web tools for research budget tracking.
# Imported from error_classifier for consistency with tool category definitions.
from ai.error_classifier import WEB_TOOLS as _WEB_TOOLS

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

# ---------------------------------------------------------------------------
# Hallucinated tool call patterns -- text that mimics tool-calling syntax
# but is NOT a structured tool_use block.  Indicates the model is not
# using the native tool-calling protocol correctly.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# TASK-240: Apologize-and-rebuild pattern detection.
# The model often says "You're absolutely right... let me start fresh" and
# then rebuilds the entire design from scratch, burning 50+ tool calls to
# reach the same failure point.  Detect this preamble text and intervene
# before the rebuild cycle begins.
# ---------------------------------------------------------------------------
_APOLOGIZE_REBUILD_PATTERNS = [
    re.compile(
        r"(?:You'?re|you are)\s+(?:absolutely|completely|totally)\s+right",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:Let me|I'?ll|I will|I am going to)\s+(?:start|rebuild|redo|begin)"
        r"\s+(?:completely\s+)?(?:fresh|from scratch|clean|over)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:I'?ve|I have)\s+been\s+making\s+the\s+same\s+(?:mistakes?|errors?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:start|rebuild|redo)\s+(?:completely\s+)?(?:from scratch|fresh|clean)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:entire|whole)\s+model\s+is\s+(?:corrupted|broken|wrong)",
        re.IGNORECASE,
    ),
]

# Minimum match count to trigger the intervention (avoid false positives
# on a single casual "let me start fresh" that is genuinely appropriate).
_APOLOGIZE_REBUILD_MIN_MATCHES = 2

# Maximum interventions per turn to avoid infinite nudge loops
_MAX_APOLOGIZE_REBUILD_INTERVENTIONS = 2

_HALLUCINATED_TOOL_PATTERNS = [
    re.compile(r"<tool_code>.*?</tool_code>", re.DOTALL),
    re.compile(r"tool_code\s*\(", re.IGNORECASE),
    re.compile(r"```tool_call\b", re.IGNORECASE),
    re.compile(r"<function_call>.*?</function_call>", re.DOTALL),
    re.compile(r"\bfunction_call\s*\(", re.IGNORECASE),
    re.compile(r"<tool_use>.*?</tool_use>", re.DOTALL),
    # Model writing tool calls as Python function calls with known tool names
    re.compile(
        r"\b(?:create_box|create_cylinder|create_sphere|execute_script|"
        r"create_sketch|extrude|revolve|take_screenshot|get_body_list|"
        r"add_fillet|add_chamfer|delete_body|save_document|undo|redo)\s*\(",
        re.IGNORECASE,
    ),
]

# Maximum consecutive hallucinated tool calls before aborting
_MAX_HALLUCINATED_TOOL_MISTAKES = 3


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

    # TASK-052: Maximum number of LLM -> tool -> LLM iterations per turn.
    # Prevents runaway agent loops from consuming unbounded resources.
    _MAX_AGENT_ITERATIONS: int = 50

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
        # TASK-049: Version counter incremented by set_conversation() so that
        # a running _run_turn_inner can detect that the conversation was
        # replaced mid-turn and avoid overwriting the new conversation.
        self._conversation_version: int = 0
        # TASK-012: Guard against concurrent _run_turn calls.  Only one
        # turn may execute at a time; a second call while a turn is in
        # progress receives an error instead of corrupting state.
        self._turn_lock = threading.Lock()
        self._emitter: Callable[[str, dict], None] | None = None
        self._conversation_id: str = str(uuid.uuid4())

        # Extract provider type early so it can be used for prompt building
        provider_type = getattr(settings, "provider", "anthropic")

        self._system_prompt: str = build_system_prompt(
            user_additions=self.settings.system_prompt,
            mode=None,  # no mode active yet
            provider=provider_type,
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

        # -- TASK-227: Script error signature tracker --
        self.script_error_tracker = ScriptErrorTracker()

        # -- TASK-230: Rebuild loop detector --
        self.rebuild_loop_detector = RebuildLoopDetector()

        # -- Mode manager (CAD mode system) --
        self.mode_manager = ModeManager()

        # -- Task manager (design plan tracking) --
        self.task_manager = TaskManager()

        # -- Checkpoint manager (design restore points) --
        self.checkpoint_manager = CheckpointManager()

        # -- Design state tracker (persistent CAD state) --
        self._design_state = DesignStateTracker()

        # -- TASK-234: Progress tracker (productive vs thrashing) --
        self._progress_tracker = ProgressTracker()

        # -- Message queue for mid-turn user input injection --
        self.message_queue = MessageQueue()

        # -- Orchestration subsystems --
        self._context_bridge = ContextBridge()
        self._subtask_manager = SubtaskManager(context_bridge=self._context_bridge)

        # -- TASK-228: Context window guard --
        self._context_window_guard = ContextWindowGuard()
        # Track whether we've already injected a pressure message this turn
        self._pressure_injected: bool = False

        # -- Provider manager (LLM backend abstraction) --
        # TASK-181: Pass the persisted provider setting at construction so
        # ProviderManager starts with the correct active_type from the
        # very first moment, avoiding transient "wrong provider" windows.
        # NOTE: provider_type is extracted earlier (before _system_prompt)
        # so it can be used for both prompt building and provider init.
        self.provider_manager = ProviderManager(initial_provider=provider_type)

        # Configure Anthropic provider
        if settings.api_key:
            self.provider_manager.configure_provider(
                "anthropic", api_key=settings.api_key
            )

        # Configure Ollama provider
        ollama_url = getattr(settings, "ollama_base_url", "http://localhost:11434")
        ollama_num_ctx = getattr(settings, "ollama_num_ctx", None)
        ollama_api_key = getattr(settings, "ollama_api_key", None)
        self.provider_manager.configure_provider(
            "ollama",
            base_url=ollama_url,
            num_ctx=ollama_num_ctx,
            api_key=ollama_api_key,
        )

        # Confirm active provider matches settings (defensive; should already
        # be set by the initial_provider argument above).
        if self.provider_manager.active_type != provider_type:
            try:
                self.provider_manager.switch(provider_type)
            except ValueError:
                logger.warning(
                    "Unknown provider '%s' in settings; defaulting to anthropic",
                    provider_type,
                )
                self.provider_manager.switch("anthropic")

        logger.info(
            "ClaudeClient initialized: provider=%s, model=%s",
            self.provider_manager.active_type,
            self._get_active_model(),
        )

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
        cancel_event: threading.Event | None = None,
    ) -> None:
        """
        Send a user message to the LLM in a background thread.

        If *on_event* is provided it is used for this turn; otherwise the
        default emitter set via set_emitter() is used.

        *cancel_event* is an optional threading.Event checked between tool
        calls to allow cooperative cancellation.
        """
        callback = on_event or self._emitter
        thread = threading.Thread(
            target=self.run_turn,
            args=(user_text, callback, cancel_event),
            daemon=True,
        )
        thread.start()

    def _reset_state(self) -> None:
        """Shared reset logic used by both clear_history and new_conversation."""
        with self._lock:
            self.conversation_history.clear()
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.turn_count = 0
        self.context_manager.reset()
        self.repetition_detector.reset()
        self.script_error_tracker.reset()
        self.rebuild_loop_detector.reset()
        self._progress_tracker.reset()
        self.task_manager.clear()
        self.checkpoint_manager.clear()
        self._design_state.reset()
        self._subtask_manager.clear()
        self._context_bridge.clear()

    def clear_history(self) -> None:
        """Reset the conversation history and token counters."""
        self._reset_state()

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
        self._reset_state()
        return self._conversation_id

    def get_conversation_id(self) -> str:
        """Return the current conversation ID."""
        return self._conversation_id

    def get_messages(self) -> list[dict[str, Any]]:
        """Return a copy of the current conversation history."""
        with self._lock:
            return list(self.conversation_history)

    # TASK-069: Public accessors to replace private attribute access
    def get_conversation_snapshot(self) -> list:
        """Return a copy of the current conversation history."""
        with self._lock:
            return list(self.conversation_history)

    def get_system_prompt(self) -> str:
        """Return the current system prompt."""
        return self._system_prompt

    def get_active_mode(self) -> str:
        """Return the active mode slug."""
        return self.mode_manager.active_slug if self.mode_manager else "full"

    def set_conversation(self, conversation_id: str, messages: list[dict[str, Any]]) -> None:
        """
        Restore a previously saved conversation.

        TASK-049: Increments ``_conversation_version`` so that any
        ``_run_turn_inner`` call that is still in-flight will detect the
        version mismatch and skip overwriting the newly set conversation.

        Parameters:
            conversation_id: The UUID of the conversation to restore.
            messages:        The full message list.
        """
        with self._lock:
            self._conversation_id = conversation_id
            self.conversation_history = list(messages)
            self._conversation_version += 1

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
        if system_prompt is not None or provider is not None:
            self._system_prompt = build_system_prompt(
                user_additions=self.settings.system_prompt,
                mode=self.mode_manager.active_slug,
                provider=self.provider_manager.active_type,
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
        # Also re-configure Ollama with current num_ctx and api_key on any provider config change
        if provider is not None and provider == "ollama":
            ollama_url = getattr(self.settings, "ollama_base_url", "http://localhost:11434")
            ollama_num_ctx = getattr(self.settings, "ollama_num_ctx", None)
            ollama_api_key = getattr(self.settings, "ollama_api_key", None)
            self.provider_manager.configure_provider(
                "ollama",
                base_url=ollama_url,
                num_ctx=ollama_num_ctx,
                api_key=ollama_api_key,
            )

    # ------------------------------------------------------------------
    # Provider-aware model resolution
    # ------------------------------------------------------------------

    def _get_active_model(self) -> str:
        """Get the model ID for the currently active provider.

        When Ollama is the active provider, returns ``settings.ollama_model``
        instead of the Anthropic model stored in ``settings.model``.
        """
        if hasattr(self, 'provider_manager') and self.provider_manager:
            active_type = self.provider_manager.active_type
            if active_type == "ollama":
                return getattr(self.settings, 'ollama_model', '') or self.settings.model
        return self.settings.model

    def _get_effective_context_window(self) -> int | None:
        """Return the actual context window size for the active provider.

        For **Ollama** models, uses ``get_context_window()`` which reads
        from cached model metadata or falls back to the default (200K).
        For **Anthropic** models, uses the known model catalog.

        TASK-242: Aligned with Roo Code's approach -- the context window
        is used for internal bookkeeping (truncation, condensation) and
        is NOT sent to Ollama as num_ctx.

        Returns ``None`` only for Anthropic models not in the catalog.
        """
        try:
            provider = self.provider_manager.active
            active_type = self.provider_manager.active_type

            if active_type == "ollama":
                model_name = self._get_active_model()
                # TASK-242: Use get_context_window() for internal bookkeeping.
                # This returns the detected contextWindow from model metadata
                # or the default (200K), matching Roo Code's pattern.
                if hasattr(provider, 'get_context_window') and model_name:
                    return provider.get_context_window(model_name)
                # Fallback: default model info
                from ai.providers.ollama_provider import OLLAMA_DEFAULT_MODEL_INFO
                return OLLAMA_DEFAULT_MODEL_INFO.get("context_window", 200_000)
            elif active_type == "anthropic":
                from ai.providers.anthropic_provider import get_effective_context_window
                model_name = self._get_active_model()
                if model_name:
                    return get_effective_context_window(model_name)
        except Exception as exc:
            logger.debug("Failed to get effective context window: %s", exc)

        return None

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def switch_provider(self, provider_type: str) -> dict:
        """Switch the active LLM provider and return info about it."""
        provider = self.provider_manager.switch(provider_type)
        # Persist to settings
        self.settings.update({"provider": provider_type})
        # Rebuild system prompt for the new provider (Ollama gets condensed prompt)
        self._system_prompt = build_system_prompt(
            user_additions=self.settings.system_prompt,
            mode=self.mode_manager.active_slug if self.mode_manager else None,
            provider=provider_type,
        )
        return {
            "type": provider_type,
            "name": provider.name,
            "is_available": provider.is_available(),
        }

    def summarize(self, messages: list, max_tokens: int = 1024) -> str | None:
        """Summarize messages using the active provider.

        Encapsulates the provider_manager chain to avoid Demeter violations
        in callers like context_manager._llm_summarize().
        Returns None if summarization is not available.
        """
        try:
            if not self.provider_manager or not self.provider_manager.active:
                return None
            if not self.provider_manager.active.is_available():
                return None
            response = self.provider_manager.active.create_message(
                messages=messages,
                max_tokens=max_tokens,
            )
            return response.content if response else None
        except Exception:
            return None

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
            provider=self.provider_manager.active_type,
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
        """Restore to a previously saved design checkpoint.

        TASK-017: The checkpoint_manager.restore() now truncates the
        messages list in-place atomically with the timeline rollback.
        We still acquire _turn_lock to prevent concurrent turns from
        interfering with the restore operation.
        """
        # TASK-012 / TASK-017: Acquire turn lock for the restore
        with self._turn_lock:
            result = self.checkpoint_manager.restore(
                name, self.mcp_server, self.conversation_history,
            )
            # restore() now mutates self.conversation_history in-place;
            # no separate truncation step needed.
        return result

    def list_checkpoints(self) -> list[dict]:
        """List all saved design checkpoints."""
        return self.checkpoint_manager.list_all()

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    @property
    def subtask_manager(self):
        """Access the subtask manager for orchestrated workflows."""
        return self._subtask_manager

    @property
    def context_bridge(self):
        """Access the context bridge for subtask context assembly."""
        return self._context_bridge

    def create_orchestrated_plan(self, title: str, steps: list) -> None:
        """Create an orchestrated design plan with dependencies and mode hints.

        This delegates to TaskManager.create_orchestrated_plan() and also
        clears the SubtaskManager and ContextBridge for a fresh workflow.

        Args:
            title: Plan title
            steps: List of step dicts, each with:
                  - 'description': str (required)
                  - 'mode_hint': Optional[str]
                  - 'depends_on': Optional[List[int]]
        """
        self.task_manager.create_orchestrated_plan(title, steps)
        self._subtask_manager.clear()
        self._context_bridge.clear()
        logger.info("Created orchestrated plan: %s with %d steps", title, len(steps))
        self._emit(self._emitter, "orchestrated_plan_created", {
            "title": title,
            "steps": len(steps),
            "summary": self.task_manager.get_plan_summary()
        })

    def execute_next_subtask(self, additional_instructions: str = "") -> dict:
        """Execute the next ready subtask in the orchestrated plan.

        Uses TaskManager.auto_advance() to find the next step, then
        delegates to SubtaskManager.execute_subtask().

        Args:
            additional_instructions: Extra instructions to pass to the subtask

        Returns:
            Dict with subtask result info:
            {
                "step_index": int,
                "status": str,
                "result": str,
                "mode": str,
                "duration": float,
                "plan_summary": dict
            }

        Raises:
            RuntimeError: If already executing a subtask
            ValueError: If no steps are ready
        """
        if not self.task_manager.has_plan:
            raise ValueError("No orchestrated plan exists")

        next_step = self.task_manager.auto_advance()
        if next_step is None:
            if self.task_manager.is_complete:
                raise ValueError("All steps in the plan are complete")
            raise ValueError("No steps are ready (dependencies not satisfied or all steps in progress/failed)")

        return self.execute_subtask(
            step_index=next_step.index,
            additional_instructions=additional_instructions
        )

    def execute_subtask(self, step_index: int, additional_instructions: str = "") -> dict:
        """Execute a specific subtask step.

        Args:
            step_index: The step index to execute
            additional_instructions: Extra instructions

        Returns:
            Dict with subtask result info
        """
        def emit_callback(event_name, data):
            self._emit(self._emitter, event_name, data)

        result = self._subtask_manager.execute_subtask(
            client=self,
            task_manager=self.task_manager,
            step_index=step_index,
            design_state_tracker=self._design_state,
            additional_instructions=additional_instructions,
            emit_callback=emit_callback
        )

        # Emit plan progress update
        self._emit(self._emitter, "orchestration_progress", {
            "step_index": result.step_index,
            "status": result.status.value,
            "result": result.result_text,
            "plan_summary": self.task_manager.get_plan_summary()
        })

        return {
            "step_index": result.step_index,
            "status": result.status.value,
            "result": result.result_text,
            "mode": result.mode_used,
            "duration": result.duration_seconds,
            "error": result.error,
            "plan_summary": self.task_manager.get_plan_summary()
        }

    def execute_full_plan(self, additional_instructions: str = "") -> dict:
        """Execute all remaining steps in the orchestrated plan sequentially.

        Runs auto_advance -> execute -> repeat until no more steps are ready.
        Stops early if a step fails and cannot be retried.

        Args:
            additional_instructions: Extra instructions for all subtasks

        Returns:
            Dict with overall execution summary:
            {
                "completed": int,
                "failed": int,
                "total_steps": int,
                "results": List[dict],
                "plan_summary": dict,
                "execution_summary": dict
            }
        """
        if not self.task_manager.has_plan:
            raise ValueError("No orchestrated plan exists")

        results = []

        self._emit(self._emitter, "orchestration_started", {
            "plan_summary": self.task_manager.get_plan_summary()
        })

        while True:
            next_step = self.task_manager.auto_advance()
            if next_step is None:
                break

            try:
                result = self.execute_subtask(
                    step_index=next_step.index,
                    additional_instructions=additional_instructions
                )
                results.append(result)

                # If step failed, try retry
                if result["status"] == "failed":
                    if self.task_manager.can_retry(next_step.index):
                        self.task_manager.retry_step(next_step.index)
                        self._emit(self._emitter, "subtask_retrying", {
                            "step_index": next_step.index,
                            "retry_count": self.task_manager.get_tasks()[next_step.index].retry_count
                        })
                        # Don't break -- the while loop will pick it up again
                    else:
                        logger.warning("Step %d failed and cannot be retried, stopping plan execution", next_step.index)
                        break
            except Exception as e:
                logger.error("Error executing step %d: %s", next_step.index, e)
                results.append({
                    "step_index": next_step.index,
                    "status": "failed",
                    "result": "",
                    "mode": next_step.mode_hint or "full",
                    "duration": 0,
                    "error": str(e),
                    "plan_summary": self.task_manager.get_plan_summary()
                })
                break

        summary = {
            "completed": sum(1 for r in results if r["status"] == "completed"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
            "total_steps": len(self.task_manager.get_tasks()),
            "results": results,
            "plan_summary": self.task_manager.get_plan_summary(),
            "execution_summary": self._subtask_manager.get_execution_summary()
        }

        self._emit(self._emitter, "orchestration_completed", summary)

        return summary

    def get_orchestration_status(self) -> dict:
        """Get current orchestration status.

        Returns:
            Dict with:
            - has_plan: bool
            - is_executing: bool
            - current_step: Optional[int]
            - plan_summary: Optional[dict]
            - execution_summary: dict
        """
        return {
            "has_plan": self.task_manager.has_plan,
            "is_executing": self._subtask_manager.is_executing,
            "current_step": self._subtask_manager.current_step,
            "plan_summary": self.task_manager.get_plan_summary() if self.task_manager.has_plan else None,
            "execution_summary": self._subtask_manager.get_execution_summary()
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

    @staticmethod
    def _detect_hallucinated_tool_calls(text: str) -> list[str]:
        """Detect text patterns that look like tool calls but are not.

        Returns a list of matched pattern snippets (empty if none detected).
        Used to identify models that are hallucinating tool-call syntax
        instead of using the native tool-calling protocol.
        """
        if not text or len(text) < 10:
            return []
        matches = []
        for pattern in _HALLUCINATED_TOOL_PATTERNS:
            found = pattern.search(text)
            if found:
                # Extract a short snippet for the warning
                snippet = found.group(0)[:80]
                matches.append(snippet)
        return matches

    @staticmethod
    def _detect_apologize_rebuild(text: str) -> int:
        """Count how many apologize-and-rebuild patterns match in *text*.

        TASK-240: Detects the "You're absolutely right... let me start
        fresh" pattern that precedes wasteful design rebuilds.

        Returns the number of matching patterns (0 = no match).
        """
        if not text or len(text) < 30:
            return 0
        count = 0
        for pattern in _APOLOGIZE_REBUILD_PATTERNS:
            if pattern.search(text):
                count += 1
        return count

    def _track_usage(self, response, on_event) -> None:
        """Accumulate token counts from an LLMResponse and emit a USAGE event."""
        input_tokens = response.usage.get("input_tokens", 0)
        output_tokens = response.usage.get("output_tokens", 0)
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.turn_count += 1

        # Context window info for progress bar
        effective_ctx = self._get_effective_context_window()

        self._emit(on_event, EventType.USAGE, {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "turn_count": self.turn_count,
            "context_window": effective_ctx,
            "max_tokens": self.settings.max_tokens,
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

    def run_turn(
        self,
        user_text: str,
        on_event: Callable[[str, dict], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Full agentic loop: send -> handle tool calls -> send results -> repeat.

        TASK-012: Acquires ``_turn_lock`` for the duration of the turn.
        If another turn is already in progress the call returns immediately
        with an error event instead of corrupting conversation state.

        TASK-033: Renamed from ``_run_turn`` to ``run_turn`` (public API).

        *cancel_event* is an optional threading.Event checked between tool
        calls and at the start of each iteration to allow cooperative
        cancellation.
        """
        # TASK-012: Reject concurrent turns
        if not self._turn_lock.acquire(blocking=False):
            self._emit(on_event, EventType.ERROR, {
                "message": (
                    "A turn is already in progress. Please wait for "
                    "the current response to complete before sending "
                    "another message."
                ),
            })
            self._emit(on_event, EventType.DONE, {})
            return

        try:
            self._run_turn_inner(user_text, on_event, cancel_event=cancel_event)
        finally:
            # TASK-012: Always release so subsequent turns can proceed
            self._turn_lock.release()

    def _patch_interrupted_tool_results(self, messages: list) -> list:
        """Fill missing tool_result blocks for interrupted turns.

        When a turn is cancelled mid-tool-loop, the API conversation history
        can have assistant messages with tool_use blocks that never got
        tool_result responses.  This causes API errors on the next turn.
        """
        if not messages:
            return messages

        # Find the last assistant message
        last_assistant_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant":
                last_assistant_idx = i
                break

        if last_assistant_idx is None:
            return messages

        last_assistant = messages[last_assistant_idx]
        content = last_assistant.get("content", [])
        if isinstance(content, str):
            return messages

        # Find tool_use blocks in the last assistant message
        tool_use_ids = set()
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_use_ids.add(block["id"])

        if not tool_use_ids:
            return messages

        # Check if there's a following user message with tool_results
        if last_assistant_idx + 1 < len(messages):
            next_msg = messages[last_assistant_idx + 1]
            if next_msg.get("role") == "user":
                next_content = next_msg.get("content", [])
                if isinstance(next_content, list):
                    for block in next_content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            tool_use_ids.discard(block.get("tool_use_id"))

        # If all tool_use blocks have results, nothing to patch
        if not tool_use_ids:
            return messages

        # Create patch results for orphaned tool_use blocks
        logger.warning("Patching %d interrupted tool_use blocks", len(tool_use_ids))
        patch_results = []
        for tool_use_id in tool_use_ids:
            patch_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": "[Tool call interrupted by user before completion]",
                "is_error": True,
            })

        # Add as a new user message after the assistant message
        if last_assistant_idx + 1 < len(messages) and messages[last_assistant_idx + 1].get("role") == "user":
            # Merge into existing user message
            existing = messages[last_assistant_idx + 1].get("content", [])
            if isinstance(existing, list):
                messages[last_assistant_idx + 1]["content"] = existing + patch_results
            else:
                messages[last_assistant_idx + 1]["content"] = patch_results
        else:
            # Insert new user message
            messages.insert(last_assistant_idx + 1, {
                "role": "user",
                "content": patch_results,
            })

        return messages

    def _run_turn_inner(
        self,
        user_text: str,
        on_event: Callable[[str, dict], None] | None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Inner implementation of the agentic loop (called under _turn_lock).

        TASK-049: Captures ``_conversation_version`` at start and checks it
        before every write-back to ``self.conversation_history``.  If the
        version has changed (i.e. ``set_conversation()`` was called
        mid-turn), the write-back is skipped to avoid overwriting the newly
        loaded conversation.

        TASK-052: Enforces ``_MAX_AGENT_ITERATIONS`` to prevent runaway
        agent loops.

        TASK-223: Injects an early warning when the iteration count reaches
        a configurable threshold (default 80%) of the maximum.

        TASK-224: Tracks consecutive web research failures and injects a
        budget-exhaustion message after a configurable threshold.
        """

        # Reset per-turn screenshot budget
        self._screenshot_count = 0
        # TASK-228: Reset pressure injection flag for this turn
        self._pressure_injected = False
        # TASK-234: Reset progress tracker for this turn
        self._progress_tracker.reset()
        # TASK-236: Empty response counter for consecutive empty responses
        _empty_response_count = 0
        # TASK-238: Track termination reason for failure report
        _termination_reason = "normal"
        # TASK-239: Hallucinated tool call counter
        _hallucinated_tool_count = 0
        # TASK-240: Apologize-and-rebuild intervention counter
        _apologize_rebuild_count = 0

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

        # TASK-049: Capture conversation version at start of turn.
        # If set_conversation() is called while this turn is running,
        # the version will increment and we will skip write-backs.
        with self._lock:
            turn_version = self._conversation_version

        # Append user message to history
        with self._lock:
            # TASK-049: Re-check version; abort if conversation was replaced
            if self._conversation_version != turn_version:
                logger.warning("Conversation replaced before user message append; aborting turn")
                self._emit(on_event, EventType.DONE, {})
                return
            self.conversation_history.append({"role": "user", "content": user_text})
            messages = list(self.conversation_history)

        # Patch any orphaned tool_use blocks from a previously interrupted turn
        messages = self._patch_interrupted_tool_results(messages)

        # ---- TASK-228: Context window adequacy check at turn start ----
        try:
            num_tools = len(self._get_filtered_tools())
            sys_prompt_tokens = self._context_window_guard.estimate_tokens(
                self._build_effective_prompt()
            )
            effective_ctx = self._get_effective_context_window()
            adequacy = self._context_window_guard.check_adequacy(
                max_tokens=self.settings.max_tokens,
                num_tools=num_tools,
                system_prompt_tokens=sys_prompt_tokens,
                message_count=len(messages),
                context_window=effective_ctx,
            )
            if adequacy.level == AdequacyLevel.CRITICAL:
                logger.warning(
                    "TASK-228: Context window CRITICAL: %s",
                    "; ".join(adequacy.reasons),
                )
                self._emit(on_event, "context_window_warning", {
                    "level": "critical",
                    **adequacy.to_dict(),
                })
                # Inject conciseness message at start of conversation
                messages.append({
                    "role": "user",
                    "content": CRITICAL_CONCISENESS_MESSAGE,
                })
            elif adequacy.level == AdequacyLevel.WARNING:
                logger.info(
                    "TASK-228: Context window WARNING: %s",
                    "; ".join(adequacy.reasons),
                )
                self._emit(on_event, "context_window_warning", {
                    "level": "warning",
                    **adequacy.to_dict(),
                })
        except Exception as exc:
            logger.debug("TASK-228: Context window check failed: %s", exc)

        # Agentic loop -- keep going while the LLM wants to call tools
        auto_continue_count = 0
        # TASK-052: Iteration counter to enforce _MAX_AGENT_ITERATIONS
        iteration_count = 0
        # TASK-223: Track whether the early warning has already been injected
        _iteration_warning_injected = False
        # TASK-224: Research budget -- track consecutive web failures
        _consecutive_web_failures = 0
        _web_budget_exhausted = False
        while True:
            # TASK-052: Guard against runaway agent loops
            iteration_count += 1

            # Check cancellation at start of each iteration
            if cancel_event and cancel_event.is_set():
                logger.info("Turn cancelled at iteration %d", iteration_count)
                _termination_reason = "user_cancel"
                if on_event:
                    self._emit(on_event, "status_update", {"message": "Cancelled by user"})
                # Version-checked write-back before breaking
                with self._lock:
                    if self._conversation_version == turn_version:
                        self.conversation_history = messages
                break

            if iteration_count > self._MAX_AGENT_ITERATIONS:
                _termination_reason = "iteration_limit"
                logger.warning(
                    "TASK-052: Agent loop hit max iterations (%d). "
                    "Injecting wrap-up message and breaking.",
                    self._MAX_AGENT_ITERATIONS,
                )
                messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM] You have reached the maximum number of "
                        "tool-call iterations ("
                        + str(self._MAX_AGENT_ITERATIONS)
                        + "). You MUST stop calling tools now and provide "
                        "a final summary to the user explaining what was "
                        "accomplished and what remains."
                    ),
                })
                # Write back and break
                with self._lock:
                    if self._conversation_version == turn_version:
                        self.conversation_history = messages
                break

            # ---- TASK-223: Early warning at configurable threshold ----
            if not _iteration_warning_injected:
                try:
                    warning_threshold = float(
                        self.settings.get(
                            "agent_iteration_warning_threshold", 0.80,
                        )
                    )
                except (TypeError, ValueError):
                    warning_threshold = 0.80
                warning_iteration = int(
                    self._MAX_AGENT_ITERATIONS * warning_threshold
                )
                if iteration_count >= warning_iteration:
                    remaining = self._MAX_AGENT_ITERATIONS - iteration_count
                    logger.info(
                        "TASK-223: Iteration warning at %d/%d (%d remaining)",
                        iteration_count,
                        self._MAX_AGENT_ITERATIONS,
                        remaining,
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[SYSTEM] Warning: You have used "
                            f"{iteration_count}/{self._MAX_AGENT_ITERATIONS} "
                            f"tool calls. {remaining} remaining. Plan to "
                            f"reach a stable stopping point soon. Summarize "
                            f"progress and what remains."
                        ),
                    })
                    _iteration_warning_injected = True

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
                # TASK-049: Version-checked write-back after condensation
                with self._lock:
                    if self._conversation_version == turn_version:
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

            # ---- TASK-228: Runtime context pressure monitoring ----
            try:
                effective_ctx = self._get_effective_context_window()
                pressure = self._context_window_guard.check_pressure(
                    max_tokens=self.settings.max_tokens,
                    messages=messages,
                    system_prompt=self._build_effective_prompt(),
                    num_tools=len(self._get_filtered_tools()),
                    context_window=effective_ctx,
                )
                if pressure.level == AdequacyLevel.CRITICAL and not self._pressure_injected:
                    logger.warning(
                        "TASK-228: Context pressure CRITICAL at %.0f%%",
                        pressure.usage_pct * 100,
                    )
                    self._emit(on_event, "context_pressure", pressure.to_dict())
                    messages.append({
                        "role": "user",
                        "content": CONTEXT_PRESSURE_MESSAGE,
                    })
                    self._pressure_injected = True
                elif pressure.level == AdequacyLevel.WARNING:
                    logger.info(
                        "TASK-228: Context pressure WARNING at %.0f%%",
                        pressure.usage_pct * 100,
                    )
                    self._emit(on_event, "context_pressure", pressure.to_dict())
            except Exception as exc:
                logger.debug("TASK-228: Context pressure check failed: %s", exc)

            # ----------------------------------------------------------------
            # TASK-236: Detect empty assistant responses
            # ----------------------------------------------------------------
            response_content = response.content
            is_empty_response = (
                response_content is None
                or response_content == ""
                or response_content == []
                or (
                    isinstance(response_content, list)
                    and not any(
                        isinstance(b, dict)
                        and b.get("type") in ("text", "tool_use")
                        for b in response_content
                    )
                )
            )
            if is_empty_response:
                _empty_response_count += 1
                logger.warning(
                    "TASK-236: Empty assistant response detected "
                    "(consecutive count: %d)",
                    _empty_response_count,
                )
                if _empty_response_count >= 2:
                    _termination_reason = "empty_responses"
                    # Graceful termination on second consecutive empty response
                    summary = self._design_state.to_summary_string()
                    termination_msg = (
                        f"Agent produced empty responses. Session terminated. "
                        f"Progress: {summary}"
                    )
                    logger.warning(
                        "TASK-236: Second consecutive empty response; "
                        "terminating agent loop. %s",
                        termination_msg,
                    )
                    self._emit(on_event, EventType.TEXT_DONE, {
                        "full_text": termination_msg,
                    })
                    messages.append({
                        "role": "assistant",
                        "content": [{"type": "text", "text": termination_msg}],
                    })
                    with self._lock:
                        if self._conversation_version == turn_version:
                            self.conversation_history = messages
                    break

                # First empty response -- retry with nudge
                nudge = (
                    "[SYSTEM] Your previous response was empty. Please "
                    "continue with the task, or explain what went wrong."
                )
                messages.append({
                    "role": "assistant",
                    "content": [],
                })
                messages.append({
                    "role": "user",
                    "content": nudge,
                })
                logger.info("TASK-236: Injecting empty-response nudge")
                continue  # retry the API call
            else:
                # Non-empty response resets the counter
                _empty_response_count = 0

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
                # -- TASK-240: Apologize-and-rebuild detection --
                # Catches the "You're absolutely right, let me start fresh"
                # pattern that precedes wasteful full rebuilds.
                if full_text and _apologize_rebuild_count < _MAX_APOLOGIZE_REBUILD_INTERVENTIONS:
                    ar_matches = self._detect_apologize_rebuild(full_text)
                    if ar_matches >= _APOLOGIZE_REBUILD_MIN_MATCHES:
                        _apologize_rebuild_count += 1
                        rebuild_count = self.rebuild_loop_detector.count
                        logger.warning(
                            "TASK-240: Apologize-and-rebuild pattern detected "
                            "(%d matches, intervention %d/%d, rebuilds=%d)",
                            ar_matches,
                            _apologize_rebuild_count,
                            _MAX_APOLOGIZE_REBUILD_INTERVENTIONS,
                            rebuild_count,
                        )
                        self._emit(on_event, "warning", {
                            "message": (
                                f"[REBUILD LOOP] The model is apologizing and "
                                f"planning to rebuild from scratch (attempt "
                                f"{rebuild_count + 1}). Intervening to prevent "
                                f"wasted iterations."
                            ),
                        })
                        messages.append({
                            "role": "user",
                            "content": (
                                "[SYSTEM -- REBUILD LOOP INTERVENTION] You are "
                                "about to rebuild the design from scratch. This "
                                "wastes your iteration budget and the same errors "
                                "will recur. DO NOT call new_document or delete "
                                "all bodies. Instead: (1) identify the SPECIFIC "
                                "step that failed, (2) explain the root cause to "
                                "the user, (3) fix ONLY that step. If you cannot "
                                "fix it, ask the user for guidance."
                            ),
                        })
                        continue  # Retry with the intervention message

                # -- Hallucinated tool call detection --
                hallucinated = self._detect_hallucinated_tool_calls(full_text)
                if hallucinated:
                    _hallucinated_tool_count += 1
                    logger.warning(
                        "TASK-239: Hallucinated tool call detected "
                        "(consecutive: %d/%d): %s",
                        _hallucinated_tool_count,
                        _MAX_HALLUCINATED_TOOL_MISTAKES,
                        hallucinated[0][:60],
                    )
                    self._emit(on_event, "warning", {
                        "message": (
                            f"[HALLUCINATED TOOL CALL] The model wrote tool-call "
                            f"syntax as plain text instead of using native tool "
                            f"calling. This usually means the model is overwhelmed. "
                            f"Attempt {_hallucinated_tool_count}/"
                            f"{_MAX_HALLUCINATED_TOOL_MISTAKES}."
                        ),
                    })

                    if _hallucinated_tool_count >= _MAX_HALLUCINATED_TOOL_MISTAKES:
                        # Abort -- model cannot use tools properly
                        _termination_reason = "hallucinated_tools"
                        abort_msg = (
                            "The model produced plain-text tool calls "
                            f"{_hallucinated_tool_count} times instead of using "
                            "the native tool-calling protocol. This typically "
                            "means the model's context window is too small for "
                            "this task, or the model does not support tool "
                            "calling. Try: (1) setting ollama_num_ctx to a "
                            "larger value, (2) using a model that supports "
                            "tool calling (e.g. qwen2.5, llama3.1, mistral), "
                            "or (3) simplifying the task."
                        )
                        self._emit(on_event, EventType.TEXT_DONE, {
                            "full_text": abort_msg,
                        })
                        messages.append({
                            "role": "assistant",
                            "content": [{"type": "text", "text": abort_msg}],
                        })
                        with self._lock:
                            if self._conversation_version == turn_version:
                                self.conversation_history = messages
                        break

                    # Inject correction message and retry
                    messages.append({
                        "role": "user",
                        "content": (
                            "[SYSTEM] You wrote tool calls as plain text instead "
                            "of using the native tool-calling protocol. Do NOT "
                            "write tool names as function calls in your text. "
                            "Instead, use the tool-calling mechanism provided by "
                            "the API. Simply decide which tool to call and "
                            "provide its name and arguments -- the system will "
                            "handle the rest. Try again now."
                        ),
                    })
                    continue  # Retry the API call

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

                # TASK-049: Version-checked write-back (no tool calls path)
                with self._lock:
                    if self._conversation_version == turn_version:
                        self.conversation_history = messages
                    else:
                        logger.info(
                            "Conversation version changed mid-turn; "
                            "skipping write-back to preserve new conversation"
                        )
                break

            # ----------------------------------------------------------------
            # Execute tool calls and build tool_result blocks
            # ----------------------------------------------------------------
            tool_results: list[dict[str, Any]] = []
            raw_results: list[tuple[str, dict]] = []  # (tool_name, raw_result)

            for tc in tool_calls:
                # Check for cancellation between tool calls
                if cancel_event and cancel_event.is_set():
                    logger.info("Turn cancelled between tool calls")
                    if on_event:
                        self._emit(on_event, "status_update", {"message": "Cancelled by user"})
                    break

                # Check for queued user messages (mid-turn injection)
                if self.message_queue.has_messages():
                    queued = self.message_queue.drain()
                    for qm in queued:
                        logger.info("Injecting mid-turn user message: %s", qm.text[:100])
                        if on_event:
                            self._emit(on_event, "status_update", {
                                "message": "User feedback received, redirecting...",
                            })
                    # Build injection text, filtering out any empty messages that
                    # slipped through (guards against Anthropic 400 "non-empty content")
                    injection_parts = [
                        f"[User feedback during turn]: {qm.text}"
                        for qm in queued
                        if qm.text and qm.text.strip()
                    ]
                    if injection_parts:
                        injection_text = "\n".join(injection_parts)
                        # Break the tool loop to let the model process the feedback
                        with self._lock:
                            if self._conversation_version == turn_version:
                                self.conversation_history.append({
                                    "role": "user",
                                    "content": injection_text,
                                })
                    else:
                        logger.warning(
                            "Mid-turn injection skipped: all %d queued messages were empty",
                            len(queued),
                        )
                    break  # Exit tool loop to process user feedback

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

                # TASK-022: If force_stop is flagged, block the call entirely
                # and return an error result instead of executing it.
                if rep_check.get("force_stop"):
                    result = {
                        "success": False,
                        "error": (
                            f"[BLOCKED] Tool '{tc_name}' has been called "
                            f"{rep_check['count']} times with identical arguments. "
                            f"The call was blocked to prevent an infinite loop. "
                            f"You MUST try a different approach."
                        ),
                        "repetition_warning": warning_msg,
                        "suggested_alternatives": rep_check.get("suggested_alternatives", ""),
                        "_force_stop": True,
                    }
                    logger.warning(
                        "TASK-022: Blocked repeated tool call '%s' (count=%d)",
                        tc_name, rep_check["count"],
                    )
                else:
                    # -- Execute the tool --
                    result = self.mcp_server.execute_tool(tc_name, tc_input)

                # -- Inject repetition warning into result --
                if rep_check["repeated"] and isinstance(result, dict):
                    result["repetition_warning"] = warning_msg
                    if rep_check.get("suggested_alternatives"):
                        result["suggested_alternatives"] = rep_check["suggested_alternatives"]
                    # TASK-062: Only set _force_stop when force_stop is actually True.
                    # Previously, type == "identical" alone would set _force_stop,
                    # causing legitimate tool calls to be flagged.
                    if rep_check.get("force_stop"):
                        result["_force_stop"] = True
                    elif rep_check.get("type") == "identical":
                        # Warn but do NOT set _force_stop
                        pass

                # -- TASK-230: Rebuild loop detection for new_document --
                if tc_name == "new_document" and isinstance(result, dict):
                    rebuild_warning = self.rebuild_loop_detector.record_new_document(
                        self.script_error_tracker,
                    )
                    if rebuild_warning:
                        result["rebuild_warning"] = rebuild_warning
                        self._emit(on_event, "warning", {
                            "message": rebuild_warning,
                        })
                        logger.warning(
                            "TASK-230: Rebuild loop detected: %s",
                            rebuild_warning[:120],
                        )

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

                        # TASK-227: Track script error signature for
                        # repeated-error detection.  The tracker inspects
                        # error_details.script_error which was just set.
                        script_rep = self.script_error_tracker.record_error(result)
                        if script_rep["repeated"]:
                            result["script_error_repeated"] = True
                            result["script_error_count"] = script_rep["count"]
                            result["script_error_message"] = script_rep["message"]
                            if script_rep.get("correction_hint"):
                                result["script_error_correction"] = script_rep["correction_hint"]
                            self._emit(on_event, "warning", {
                                "message": script_rep["message"],
                            })
                            logger.warning(
                                "TASK-227: Script error repeated %dx: %s",
                                script_rep["count"],
                                script_rep.get("signature"),
                            )
                        if script_rep.get("blocked"):
                            result["_force_stop"] = True

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

                        # TASK-229: Inject compact diagnostic summary so
                        # the LLM sees body/sketch state without scripting.
                        diag_summary = format_diagnostic_summary(diagnostic_data)
                        if diag_summary:
                            result['diagnostic_summary'] = diag_summary
                            logger.debug(
                                "TASK-229: Injected diagnostic summary: %s",
                                diag_summary[:120],
                            )

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

                    except Exception as e:
                        # TASK-025: Log verification delta errors instead of silently swallowing
                        logger.exception(
                            "Verification delta computation failed for tool '%s'", tc_name,
                        )
                        if isinstance(result, dict):
                            result["verification_error"] = str(e)

                # -- TASK-237: Deduplicate script error fields before
                # serialization to save tokens in context window --
                if (
                    tc_name == "execute_script"
                    and isinstance(result, dict)
                    and not result.get("success", True)
                ):
                    result = deduplicate_script_error(result)

                # -- TASK-234: Track progress for this tool call --
                progress_warning = self._progress_tracker.record(
                    tc_name, result if isinstance(result, dict) else None,
                )
                if progress_warning:
                    self._emit(on_event, "warning", {
                        "message": progress_warning,
                    })

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

            # ---- TASK-234: Inject thrashing warning into conversation ----
            # Check if a thrashing warning should be injected as a system
            # message (the per-tool warning was already emitted above;
            # this injects it into the conversation for the LLM to see).
            progress_stats = self._progress_tracker.to_dict()
            if (
                progress_stats["total_calls"] >= 10
                and progress_stats["thrashing_ratio"] > 0.6
            ):
                thrashing_msg = (
                    f"[THRASHING WARNING] Only "
                    f"{progress_stats['productive_count']}/"
                    f"{progress_stats['total_calls']} tool calls produced "
                    f"lasting geometry. "
                    f"{progress_stats['thrashing_count']} calls were "
                    f"undos/deletes/failures. Consider changing your approach."
                )
                # Only inject once -- the ProgressTracker.record() returns
                # the warning only on first breach, so check if we already
                # injected (the warning_emitted flag is internal, but we
                # can check by looking at progress_warning from the loop).
                _already_in_messages = any(
                    isinstance(m.get("content"), str)
                    and "[THRASHING WARNING]" in m["content"]
                    for m in messages
                )
                if not _already_in_messages:
                    messages.append({
                        "role": "user",
                        "content": thrashing_msg,
                    })

            # ---- TASK-224: Research budget tracking ----
            # Check each executed tool for web research failures.
            # A "failure" is a web tool returning empty results or an error.
            for _rbt_name, _rbt_result in raw_results:
                if _rbt_name in _WEB_TOOLS:
                    _web_failed = False
                    if isinstance(_rbt_result, dict):
                        # web_search returning empty results
                        if (
                            _rbt_result.get("status") == "success"
                            and not _rbt_result.get("results")
                        ):
                            _web_failed = True
                        # web_search or web_fetch returning error
                        elif _rbt_result.get("status") == "error":
                            _web_failed = True
                        elif not _rbt_result.get("success", True):
                            _web_failed = True
                    if _web_failed:
                        _consecutive_web_failures += 1
                    else:
                        # Successful web call resets the counter
                        _consecutive_web_failures = 0
                else:
                    # Non-web tool call resets the counter
                    _consecutive_web_failures = 0

            if (
                not _web_budget_exhausted
                and _consecutive_web_failures > 0
            ):
                try:
                    _web_max_failures = int(
                        self.settings.get(
                            "web_research_max_consecutive_failures", 3,
                        )
                    )
                except (TypeError, ValueError):
                    _web_max_failures = 3
                if _consecutive_web_failures >= _web_max_failures:
                    _web_budget_exhausted = True
                    logger.warning(
                        "TASK-224: Web research budget exhausted "
                        "(%d consecutive failures)",
                        _consecutive_web_failures,
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[SYSTEM] Web research budget exhausted "
                            f"({_consecutive_web_failures} consecutive "
                            f"failures). Ask the user to provide the "
                            f"information directly, or proceed using your "
                            f"internal knowledge with appropriate caveats."
                        ),
                    })

            # ---- Force-stop on identical repetition ----
            # If any tool result was flagged for force-stop, inject a strong
            # system message and break out of the tool loop to force the
            # model to respond with text instead of repeating the same call.
            force_stop = any(
                isinstance(raw, dict) and raw.get("_force_stop")
                for _, raw in raw_results
            )
            if force_stop:
                _termination_reason = "force_stop"
                # TASK-022: Inject a stronger system message that demands
                # reasoning before any further tool calls.
                messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM] Repeated tool calls detected. You MUST explain "
                        "your reasoning and propose a DIFFERENT approach before "
                        "making another tool call. If you call the same tool again "
                        "without a different strategy, the operation will be blocked. "
                        "STOP and explain to the user what is going wrong and what "
                        "alternative approaches are available."
                    ),
                })
                # TASK-049: Version-checked write-back (force-stop path)
                with self._lock:
                    if self._conversation_version == turn_version:
                        self.conversation_history = messages
                    else:
                        logger.info(
                            "Conversation version changed mid-turn; "
                            "skipping force-stop write-back"
                        )
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

        # -- TASK-238: Generate failure report if session had issues --
        try:
            report = SessionFailureReport()
            report.set_termination_reason(_termination_reason)
            report.collect(
                progress_tracker=self._progress_tracker,
                script_error_tracker=self.script_error_tracker,
                rebuild_loop_detector=self.rebuild_loop_detector,
                mcp_server=self.mcp_server,
                context_pressure_triggered=self._pressure_injected,
            )
            if report.should_generate():
                report_data = report.to_dict()
                filepath = report.save(self._conversation_id)
                self._emit(on_event, "session_failure_report", {
                    "report": report_data,
                    "file": filepath,
                })
        except Exception as exc:
            logger.debug("TASK-238: Failed to generate failure report: %s", exc)

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

        # Reasoning callback for thinking models (Qwen 3.x, DeepSeek R1)
        def on_reasoning(chunk):
            self._emit(on_event, "reasoning_delta", {"text": chunk})

        response = provider.stream_message(
            messages=messages,
            system=effective_prompt,
            tools=filtered_tools,
            max_tokens=self.settings.max_tokens,
            model=self._get_active_model(),
            text_callback=on_text,
            reasoning_callback=on_reasoning,
        )

        # Emit complete reasoning block if available
        if getattr(response, "reasoning", None):
            self._emit(on_event, "reasoning_complete", {
                "text": response.reasoning,
            })

        return response

"""Subtask execution manager for orchestrated workflows.

Manages the lifecycle of subtasks within an orchestrated plan:
state snapshot, mode switch, isolated execution, result extraction,
and state restoration.
"""

import copy
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from ai.context_bridge import ContextBridge, SubtaskContext
from ai.system_prompt import build_system_prompt

logger = logging.getLogger(__name__)


class SubtaskStatus(Enum):
    """Status of a subtask execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class SubtaskResult:
    """Result of a subtask execution."""

    step_index: int
    status: SubtaskStatus
    result_text: str  # The extracted result from the assistant's response
    mode_used: str
    messages_exchanged: int  # How many messages in the subtask conversation
    tool_calls_made: int  # How many tool calls were executed
    duration_seconds: float
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/persistence."""
        return {
            "step_index": self.step_index,
            "status": self.status.value,
            "result_text": self.result_text,
            "mode_used": self.mode_used,
            "messages_exchanged": self.messages_exchanged,
            "tool_calls_made": self.tool_calls_made,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


@dataclass
class OrchestratorState:
    """Snapshot of ClaudeClient state before a subtask runs.

    Used to restore the orchestrator's conversation after the subtask completes.
    """

    conversation_history: List[Dict[str, Any]]
    system_prompt: str
    active_mode: str


class SubtaskManager:
    """Manages subtask execution within orchestrated workflows.

    The SubtaskManager works WITH ClaudeClient (not replacing it) by:
    1. Snapshotting the orchestrator's conversation state
    2. Clearing the conversation and switching to the subtask's mode
    3. Injecting subtask context into the system prompt
    4. Running the agentic loop (via ClaudeClient.run_turn)
    5. Extracting the result from the subtask conversation
    6. Restoring the orchestrator's original state
    7. Recording the result in the ContextBridge

    This approach reuses all of ClaudeClient's infrastructure (tool execution,
    verification, error recovery, streaming) without duplicating it.
    """

    def __init__(self, context_bridge: Optional[ContextBridge] = None):
        """Initialize the SubtaskManager.

        Args:
            context_bridge: ContextBridge for assembling/recording context.
                          Created automatically if not provided.
        """
        self._context_bridge = context_bridge or ContextBridge()
        self._execution_history: List[SubtaskResult] = []
        self._is_executing: bool = False
        self._current_step: Optional[int] = None

    @property
    def context_bridge(self) -> ContextBridge:
        """Access the context bridge."""
        return self._context_bridge

    @property
    def is_executing(self) -> bool:
        """Whether a subtask is currently running."""
        return self._is_executing

    @property
    def current_step(self) -> Optional[int]:
        """The step index currently being executed, or None."""
        return self._current_step

    @property
    def execution_history(self) -> List[SubtaskResult]:
        """History of all subtask executions."""
        return list(self._execution_history)

    def snapshot_state(self, client) -> OrchestratorState:
        """Capture the current ClaudeClient state for later restoration.

        Args:
            client: The ClaudeClient instance. Accessed via duck typing:
                   - client.conversation_history (list)
                   - client._system_prompt (str)
                   - client.mode_manager._active_mode (str)

        Returns:
            OrchestratorState snapshot (deep copy of conversation_history)
        """
        return OrchestratorState(
            conversation_history=copy.deepcopy(client.get_conversation_snapshot()),
            system_prompt=client.get_system_prompt(),
            active_mode=client.get_active_mode(),
        )

    def restore_state(self, client, state: OrchestratorState) -> None:
        """Restore ClaudeClient to a previously snapshotted state.

        Args:
            client: The ClaudeClient instance
            state: The OrchestratorState to restore

        This restores:
        - conversation_history (replaces entirely)
        - _system_prompt
        - Active mode (via client.mode_manager.switch_mode if different)
        """
        client.conversation_history = state.conversation_history
        client._system_prompt = state.system_prompt
        if client.get_active_mode() != state.active_mode:
            client.mode_manager.switch_mode(state.active_mode)

    def prepare_subtask(self, client, context: SubtaskContext) -> None:
        """Prepare the ClaudeClient for subtask execution.

        This:
        1. Clears the conversation_history to empty list
        2. Switches to the subtask's mode (context.mode) via mode_manager
           directly -- NOT via client.switch_mode() which would rebuild
           the system prompt (we set our own prompt with subtask context)
        3. Builds a fresh system prompt for the target mode and appends
           the subtask context

        Args:
            client: The ClaudeClient instance
            context: The SubtaskContext with mode and context info
        """
        # 1. Clear conversation history
        client.conversation_history = []

        # 2. Switch mode directly via mode_manager (bypasses prompt rebuild)
        client.mode_manager.switch_mode(context.mode)

        # 3. Build subtask system prompt
        user_additions = ""
        if hasattr(client, "settings") and hasattr(client.settings, "system_prompt"):
            user_additions = client.settings.system_prompt or ""

        base_prompt = build_system_prompt(
            user_additions=user_additions, mode=context.mode
        )
        subtask_context_text = context.to_system_context()
        client._system_prompt = base_prompt + "\n\n" + subtask_context_text

    def execute_subtask(
        self,
        client,
        task_manager,
        step_index: Optional[int] = None,
        design_state_tracker=None,
        additional_instructions: str = "",
        emit_callback: Optional[Callable] = None,
    ) -> SubtaskResult:
        """Execute a single subtask step.

        WARNING: Must NOT be called from inside a turn that holds _turn_lock,
        or a deadlock will occur. Use execute_full_plan() which runs outside
        the turn lock.

        This is the main entry point. It orchestrates the full lifecycle:
        1. Build context via ContextBridge
        2. Snapshot orchestrator state
        3. Prepare client for subtask
        4. Update TaskManager (start_step)
        5. Run the agentic loop via client.run_turn()
        6. Extract result from the subtask conversation
        7. Record result in ContextBridge
        8. Update TaskManager (complete_step or fail_step)
        9. Restore orchestrator state
        10. Return SubtaskResult

        Args:
            client: The ClaudeClient instance
            task_manager: The TaskManager with the plan
            step_index: Specific step to execute, or None for auto_advance
            design_state_tracker: Optional DesignStateTracker
            additional_instructions: Extra instructions for this subtask
            emit_callback: Optional callback for emitting orchestration events.
                          Called as emit_callback(event_name, data_dict)

        Returns:
            SubtaskResult with the outcome

        Raises:
            RuntimeError: If a subtask is already executing, or if called
                while _turn_lock is held (deadlock prevention).
            ValueError: If no step is available to execute
        """
        if self._is_executing:
            raise RuntimeError("A subtask is already executing")

        # TASK-096: Detect potential deadlock -- if the caller's client holds
        # _turn_lock, calling run_turn() here would deadlock.
        if hasattr(client, '_turn_lock') and client._turn_lock.locked():
            raise RuntimeError(
                "Cannot execute subtask while _turn_lock is held -- deadlock risk"
            )

        # 1. Build context via ContextBridge (may raise ValueError)
        context = self._context_bridge.build_context(
            task_manager,
            design_state_tracker,
            step_index,
            additional_instructions,
        )
        resolved_step_index = context.step_index

        # Mark as executing
        self._is_executing = True
        self._current_step = resolved_step_index
        start_time = time.monotonic()

        # 2. Snapshot orchestrator state
        state = self.snapshot_state(client)

        try:
            # 3. Prepare client for subtask
            self.prepare_subtask(client, context)

            # 4. Update TaskManager
            task_manager.start_step(resolved_step_index)

            # 5. Emit started event
            if emit_callback:
                emit_callback(
                    "subtask_started",
                    {
                        "step_index": resolved_step_index,
                        "mode": context.mode,
                        "description": context.step_description,
                    },
                )

            # 6. Build synthetic user message
            user_message = (
                f"Execute the following design step:\n\n"
                f"{context.step_description}"
            )
            if context.instructions:
                user_message += f"\n\n{context.instructions}"

            # 7. Run the agentic loop with an event collector for tool calls
            tool_call_count = [0]

            def event_collector(event_type, payload):
                if event_type == "tool_call":
                    tool_call_count[0] += 1

            client.run_turn(user_message, on_event=event_collector)

            # 8. Extract result from subtask conversation
            result_text, extracted_tool_calls, message_count = (
                self._extract_result(client.conversation_history)
            )

            duration = time.monotonic() - start_time

            # Use the higher of event-counted and extracted tool calls
            actual_tool_calls = max(tool_call_count[0], extracted_tool_calls)

            # 9. Record result in ContextBridge
            self._context_bridge.record_subtask_result(
                resolved_step_index,
                context.step_description,
                result_text,
                context.mode,
            )

            # 10. Update TaskManager
            task_manager.complete_step(resolved_step_index, result_text)

            subtask_result = SubtaskResult(
                step_index=resolved_step_index,
                status=SubtaskStatus.COMPLETED,
                result_text=result_text,
                mode_used=context.mode,
                messages_exchanged=message_count,
                tool_calls_made=actual_tool_calls,
                duration_seconds=duration,
            )

            if emit_callback:
                emit_callback(
                    "subtask_completed",
                    {
                        "step_index": resolved_step_index,
                        "result": result_text,
                        "duration": duration,
                    },
                )

            self._execution_history.append(subtask_result)
            return subtask_result

        except Exception as exc:
            duration = time.monotonic() - start_time
            error_msg = str(exc)

            logger.error(
                "Subtask execution failed for step %d: %s",
                resolved_step_index,
                error_msg,
            )

            task_manager.fail_step(resolved_step_index, error_msg)

            subtask_result = SubtaskResult(
                step_index=resolved_step_index,
                status=SubtaskStatus.FAILED,
                result_text="",
                mode_used=context.mode,
                messages_exchanged=0,
                tool_calls_made=0,
                duration_seconds=duration,
                error=error_msg,
            )

            if emit_callback:
                emit_callback(
                    "subtask_failed",
                    {
                        "step_index": resolved_step_index,
                        "error": error_msg,
                        "duration": duration,
                    },
                )

            self._execution_history.append(subtask_result)
            return subtask_result

        finally:
            # ALWAYS restore orchestrator state, even on failure
            self.restore_state(client, state)
            self._is_executing = False
            self._current_step = None

    def _extract_result(
        self, conversation_history: List[Dict[str, Any]]
    ) -> Tuple[str, int, int]:
        """Extract the result text and metrics from a subtask conversation.

        Scans the conversation history to find:
        - The last assistant text response (the result)
        - Count of tool_use blocks (tool_calls_made)
        - Total messages exchanged

        Args:
            conversation_history: The subtask's conversation messages

        Returns:
            Tuple of (result_text, tool_calls_count, message_count)
        """
        if not conversation_history:
            return ("", 0, 0)

        message_count = len(conversation_history)
        tool_calls_count = 0
        result_text = ""

        # Scan all messages for tool calls and track last assistant text
        for msg in conversation_history:
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "tool_use":
                                tool_calls_count += 1
                            elif (
                                block.get("type") == "text"
                                and block.get("text")
                            ):
                                result_text = block["text"]
                elif isinstance(content, str) and content:
                    result_text = content

        # If no text result found, try to summarize from last tool results
        if not result_text:
            for msg in reversed(conversation_history):
                if msg.get("role") == "user":
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if (
                                isinstance(block, dict)
                                and block.get("type") == "tool_result"
                            ):
                                tool_content = block.get("content", "")
                                if isinstance(tool_content, str):
                                    result_text = (
                                        f"[Tool result]: {tool_content[:500]}"
                                    )
                                    break
                        if result_text:
                            break

        return (result_text, tool_calls_count, message_count)

    def get_execution_summary(self) -> Dict[str, Any]:
        """Get a summary of all subtask executions.

        Returns dict with:
        - total_executed: int
        - completed: int
        - failed: int
        - total_duration: float
        - total_tool_calls: int
        - executions: List of SubtaskResult.to_dict()
        """
        completed = sum(
            1
            for r in self._execution_history
            if r.status == SubtaskStatus.COMPLETED
        )
        failed = sum(
            1
            for r in self._execution_history
            if r.status == SubtaskStatus.FAILED
        )
        total_duration = sum(
            r.duration_seconds for r in self._execution_history
        )
        total_tool_calls = sum(
            r.tool_calls_made for r in self._execution_history
        )

        return {
            "total_executed": len(self._execution_history),
            "completed": completed,
            "failed": failed,
            "total_duration": total_duration,
            "total_tool_calls": total_tool_calls,
            "executions": [r.to_dict() for r in self._execution_history],
        }

    def clear(self) -> None:
        """Clear execution history and context bridge."""
        self._execution_history.clear()
        self._context_bridge.clear()
        self._is_executing = False
        self._current_step = None

"""Shared state container for orchestration -- breaks circular dependency between
ContextBridge and SubtaskManager (TASK-083)."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SubtaskResult:
    """Result from a completed subtask."""
    step_index: int
    status: str  # "completed", "failed", "skipped"
    result: str = ""
    mode: str = ""
    error: str = ""


@dataclass
class OrchestrationState:
    """Shared read/write state for orchestration modules.

    Both ContextBridge and SubtaskManager read from and write to this,
    eliminating the need for either to reach into the other's internals.
    """
    plan_title: str = ""
    subtask_results: list[SubtaskResult] = field(default_factory=list)
    active_step_index: int = -1
    parent_conversation_snapshot: list[dict] = field(default_factory=list)
    parent_system_prompt: str = ""
    parent_mode: str = "full"

    def add_result(self, result: SubtaskResult) -> None:
        self.subtask_results.append(result)

    def get_completed_results(self) -> list[SubtaskResult]:
        return [r for r in self.subtask_results if r.status == "completed"]

    def clear(self) -> None:
        self.subtask_results.clear()
        self.active_step_index = -1
        self.plan_title = ""

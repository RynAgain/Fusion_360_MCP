"""
ai/task_manager.py
Task decomposition and tracking for complex CAD design operations.

Provides a simple plan/step tracker that the agent and the user can
update.  The current plan is injected into the system prompt context
so that Claude stays aware of progress.
"""

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Possible states for a single design step."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class DesignTask:
    """A single step in a design plan."""

    def __init__(
        self,
        description: str,
        index: int,
        mode_hint: Optional[str] = None,
        depends_on: Optional[List[int]] = None,
        subtask_result: Optional[str] = None,
        retry_count: int = 0,
        max_retries: int = 2,
    ):
        self.id: str = str(uuid.uuid4())[:8]
        self.index: int = index
        self.description: str = description
        self.status: TaskStatus = TaskStatus.PENDING
        self.result: str = ""
        self.created_at: str = datetime.now(timezone.utc).isoformat()
        self.completed_at: str = ""
        # --- Phase 1a orchestration fields ---
        self.mode_hint: Optional[str] = mode_hint
        self.depends_on: List[int] = depends_on if depends_on is not None else []
        self.subtask_result: Optional[str] = subtask_result
        self.retry_count: int = retry_count
        self.max_retries: int = max_retries

    def to_dict(self) -> dict:
        """Serialise to a JSON-friendly dict."""
        return {
            "id": self.id,
            "index": self.index,
            "description": self.description,
            "status": self.status.value,
            "result": self.result,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "mode_hint": self.mode_hint,
            "depends_on": self.depends_on,
            "subtask_result": self.subtask_result,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }

    def to_markdown(self) -> str:
        """Render as a markdown checkbox line."""
        # Build the base description
        desc = self.description

        # Append mode hint if present
        if self.mode_hint:
            desc += f" (mode: {self.mode_hint})"

        # Append dependency info if present
        if self.depends_on:
            # Display as 1-based step numbers for readability
            dep_str = ", ".join(str(d + 1) for d in self.depends_on)
            desc += f" [depends on: {dep_str}]"

        if self.status == TaskStatus.COMPLETED:
            return f"[x] {desc}"
        elif self.status == TaskStatus.IN_PROGRESS:
            return f"[-] {desc}"
        elif self.status == TaskStatus.FAILED:
            return f"[!] {desc} (FAILED: {self.result})"
        elif self.status == TaskStatus.SKIPPED:
            return f"[~] {desc} (skipped)"
        else:
            return f"[ ] {desc}"


class TaskManager:
    """Manages multi-step design task plans."""

    def __init__(self):
        self._tasks: list[DesignTask] = []
        self._plan_title: str = ""
        self._current_step: int = -1

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def has_plan(self) -> bool:
        """True if any tasks have been created."""
        return len(self._tasks) > 0

    @property
    def current_step(self) -> int:
        """Index of the step currently in progress (-1 if none)."""
        return self._current_step

    @property
    def is_complete(self) -> bool:
        """True if every task has a terminal status."""
        return self.has_plan and all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED, TaskStatus.FAILED)
            for t in self._tasks
        )

    @property
    def progress(self) -> dict:
        """Return a summary of task counts by status."""
        if not self._tasks:
            return {
                "total": 0,
                "completed": 0,
                "in_progress": 0,
                "pending": 0,
                "failed": 0,
            }
        return {
            "total": len(self._tasks),
            "completed": sum(1 for t in self._tasks if t.status == TaskStatus.COMPLETED),
            "in_progress": sum(1 for t in self._tasks if t.status == TaskStatus.IN_PROGRESS),
            "pending": sum(1 for t in self._tasks if t.status == TaskStatus.PENDING),
            "failed": sum(1 for t in self._tasks if t.status == TaskStatus.FAILED),
        }

    # ------------------------------------------------------------------
    # Plan lifecycle
    # ------------------------------------------------------------------

    def _validate_index(self, index: int) -> bool:
        """Check whether *index* refers to a valid task position."""
        return 0 <= index < len(self._tasks)

    def create_plan(self, title: str, steps: list[str]) -> list[DesignTask]:
        """Create a new design plan from a list of step descriptions."""
        self._plan_title = title
        self._tasks = [DesignTask(desc, i) for i, desc in enumerate(steps)]
        self._current_step = -1
        logger.info("Created design plan: '%s' with %d steps", title, len(steps))
        return self._tasks

    def create_orchestrated_plan(
        self, title: str, steps: List[Dict[str, Any]]
    ) -> List[DesignTask]:
        """Create a plan with dependency and mode information.

        Each step dict can have:
        - 'description': str (required)
        - 'mode_hint': Optional[str] (suggested mode)
        - 'depends_on': Optional[List[int]] (step indices this depends on)

        Example::

            steps = [
                {"description": "Create base sketch", "mode_hint": "sketch"},
                {"description": "Extrude base", "mode_hint": "modeling", "depends_on": [0]},
                {"description": "Run stress analysis", "mode_hint": "analysis", "depends_on": [1]},
            ]
        """
        self._plan_title = title
        self._tasks = []
        for i, step in enumerate(steps):
            task = DesignTask(
                description=step["description"],
                index=i,
                mode_hint=step.get("mode_hint"),
                depends_on=step.get("depends_on", []),
            )
            self._tasks.append(task)
        self._current_step = -1
        logger.info(
            "Created orchestrated plan: '%s' with %d steps", title, len(steps)
        )
        return self._tasks

    def start_step(self, index: int | None = None) -> DesignTask | None:
        """Mark a step as in progress.  Defaults to next pending step."""
        if index is None:
            for task in self._tasks:
                if task.status == TaskStatus.PENDING:
                    index = task.index
                    break
            if index is None:
                return None  # No pending steps
        if self._validate_index(index):
            self._tasks[index].status = TaskStatus.IN_PROGRESS
            self._current_step = index
            return self._tasks[index]
        return None

    def complete_step(self, index: int, result: str = "") -> DesignTask | None:
        """Mark a step as completed."""
        if self._validate_index(index):
            self._tasks[index].status = TaskStatus.COMPLETED
            self._tasks[index].result = result
            self._tasks[index].completed_at = datetime.now(timezone.utc).isoformat()
            return self._tasks[index]
        return None

    def fail_step(self, index: int, error: str = "") -> DesignTask | None:
        """Mark a step as failed."""
        if self._validate_index(index):
            self._tasks[index].status = TaskStatus.FAILED
            self._tasks[index].result = error
            self._tasks[index].completed_at = datetime.now(timezone.utc).isoformat()
            return self._tasks[index]
        return None

    def skip_step(self, index: int) -> DesignTask | None:
        """Skip a step."""
        if self._validate_index(index):
            self._tasks[index].status = TaskStatus.SKIPPED
            return self._tasks[index]
        return None

    def clear(self) -> None:
        """Clear all tasks."""
        self._tasks.clear()
        self._plan_title = ""
        self._current_step = -1

    # ------------------------------------------------------------------
    # Dependency / orchestration helpers
    # ------------------------------------------------------------------

    def get_ready_steps(self) -> List[DesignTask]:
        """Return steps that are PENDING and have all dependencies satisfied.

        A dependency is satisfied if its status is COMPLETED or SKIPPED.
        Returns steps in index order.
        """
        satisfied = {TaskStatus.COMPLETED, TaskStatus.SKIPPED}
        ready: List[DesignTask] = []
        for task in self._tasks:
            if task.status != TaskStatus.PENDING:
                continue
            deps_met = all(
                self._validate_index(dep) and self._tasks[dep].status in satisfied
                for dep in task.depends_on
            )
            if deps_met:
                ready.append(task)
        return ready

    def auto_advance(self) -> Optional[DesignTask]:
        """Get the next step to execute automatically.

        Returns the first ready step (lowest index among
        :py:meth:`get_ready_steps`), or ``None`` if no steps are ready or
        the plan is complete.  Does **not** change the step's status -- the
        caller should call :py:meth:`start_step`.
        """
        ready = self.get_ready_steps()
        return ready[0] if ready else None

    def can_retry(self, index: int) -> bool:
        """Check if a failed step can be retried."""
        if not self._validate_index(index):
            return False
        task = self._tasks[index]
        return (
            task.status == TaskStatus.FAILED and task.retry_count < task.max_retries
        )

    def retry_step(self, index: int) -> Optional[DesignTask]:
        """Reset a FAILED step back to PENDING and increment retry_count.

        Returns the step if retry is possible, ``None`` if max retries
        exceeded or the index is invalid.
        """
        if not self.can_retry(index):
            return None
        task = self._tasks[index]
        task.retry_count += 1
        task.status = TaskStatus.PENDING
        task.result = ""
        task.completed_at = ""
        logger.info(
            "Retrying step %d ('%s'), attempt %d/%d",
            index,
            task.description,
            task.retry_count,
            task.max_retries,
        )
        return task

    def get_dependency_graph(self) -> Dict[int, List[int]]:
        """Return the dependency graph as ``{step_index: [dependency_indices]}``."""
        return {task.index: list(task.depends_on) for task in self._tasks}

    def get_plan_summary(self) -> Dict[str, Any]:
        """Return a summary dict describing the current plan state.

        Keys:
        - title, total_steps, completed, failed, in_progress, pending,
          ready, is_complete, has_failures
        """
        prog = self.progress
        return {
            "title": self._plan_title,
            "total_steps": prog["total"],
            "completed": prog["completed"],
            "failed": prog["failed"],
            "in_progress": prog["in_progress"],
            "pending": prog["pending"],
            "ready": len(self.get_ready_steps()),
            "is_complete": self.is_complete,
            "has_failures": prog["failed"] > 0,
        }

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_markdown(self) -> str:
        """Render the entire plan as a markdown checklist."""
        if not self._tasks:
            return ""
        lines = [f"## Design Plan: {self._plan_title}"]
        for task in self._tasks:
            lines.append(task.to_markdown())
        prog = self.progress
        lines.append(f"\nProgress: {prog['completed']}/{prog['total']} steps complete")

        # Append ready-step hints when there are orchestrated dependencies
        ready = self.get_ready_steps()
        if ready:
            ready_labels = ", ".join(f"Step {t.index + 1}" for t in ready)
            lines.append(f"Ready steps: {ready_labels}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialise to a JSON-friendly dict."""
        return {
            "title": self._plan_title,
            "tasks": [t.to_dict() for t in self._tasks],
            "progress": self.progress,
            "current_step": self._current_step,
            "is_complete": self.is_complete,
        }

    def get_context_injection(self) -> str:
        """Get a context string to inject into the system prompt / conversation."""
        if not self._tasks:
            return ""
        return f"\n\n---\n{self.to_markdown()}\n---\n"

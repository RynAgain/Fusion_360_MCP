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

    def __init__(self, description: str, index: int):
        self.id: str = str(uuid.uuid4())[:8]
        self.index: int = index
        self.description: str = description
        self.status: TaskStatus = TaskStatus.PENDING
        self.result: str = ""
        self.created_at: str = datetime.now(timezone.utc).isoformat()
        self.completed_at: str = ""

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
        }

    def to_markdown(self) -> str:
        """Render as a markdown checkbox line."""
        if self.status == TaskStatus.COMPLETED:
            return f"[x] {self.description}"
        elif self.status == TaskStatus.IN_PROGRESS:
            return f"[-] {self.description}"
        elif self.status == TaskStatus.FAILED:
            return f"[!] {self.description} (FAILED: {self.result})"
        elif self.status == TaskStatus.SKIPPED:
            return f"[~] {self.description} (skipped)"
        else:
            return f"[ ] {self.description}"


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

    def create_plan(self, title: str, steps: list[str]) -> list[DesignTask]:
        """Create a new design plan from a list of step descriptions."""
        self._plan_title = title
        self._tasks = [DesignTask(desc, i) for i, desc in enumerate(steps)]
        self._current_step = -1
        logger.info("Created design plan: '%s' with %d steps", title, len(steps))
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
        if 0 <= index < len(self._tasks):
            self._tasks[index].status = TaskStatus.IN_PROGRESS
            self._current_step = index
            return self._tasks[index]
        return None

    def complete_step(self, index: int, result: str = "") -> DesignTask | None:
        """Mark a step as completed."""
        if 0 <= index < len(self._tasks):
            self._tasks[index].status = TaskStatus.COMPLETED
            self._tasks[index].result = result
            self._tasks[index].completed_at = datetime.now(timezone.utc).isoformat()
            return self._tasks[index]
        return None

    def fail_step(self, index: int, error: str = "") -> DesignTask | None:
        """Mark a step as failed."""
        if 0 <= index < len(self._tasks):
            self._tasks[index].status = TaskStatus.FAILED
            self._tasks[index].result = error
            self._tasks[index].completed_at = datetime.now(timezone.utc).isoformat()
            return self._tasks[index]
        return None

    def skip_step(self, index: int) -> DesignTask | None:
        """Skip a step."""
        if 0 <= index < len(self._tasks):
            self._tasks[index].status = TaskStatus.SKIPPED
            return self._tasks[index]
        return None

    def clear(self) -> None:
        """Clear all tasks."""
        self._tasks.clear()
        self._plan_title = ""
        self._current_step = -1

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

"""
tests/test_task_manager.py
Tests for the task decomposition and tracking system.
"""

import pytest

from ai.task_manager import TaskManager, TaskStatus, DesignTask


class TestDesignTask:
    """Tests for the DesignTask class."""

    def test_construction(self):
        """DesignTask initialises with the expected defaults."""
        task = DesignTask("Draw a circle", 0)
        assert task.description == "Draw a circle"
        assert task.index == 0
        assert task.status == TaskStatus.PENDING
        assert task.result == ""
        assert task.completed_at == ""
        assert len(task.id) == 8
        assert task.created_at != ""

    def test_to_dict(self):
        """to_dict produces a complete serialisation."""
        task = DesignTask("Test step", 2)
        d = task.to_dict()
        assert d["description"] == "Test step"
        assert d["index"] == 2
        assert d["status"] == "pending"
        assert d["result"] == ""

    def test_to_markdown_pending(self):
        """Pending tasks render as unchecked."""
        task = DesignTask("Pending step", 0)
        assert task.to_markdown() == "[ ] Pending step"

    def test_to_markdown_completed(self):
        """Completed tasks render as checked."""
        task = DesignTask("Done step", 0)
        task.status = TaskStatus.COMPLETED
        assert task.to_markdown() == "[x] Done step"

    def test_to_markdown_in_progress(self):
        """In-progress tasks render with dash."""
        task = DesignTask("Working step", 0)
        task.status = TaskStatus.IN_PROGRESS
        assert task.to_markdown() == "[-] Working step"

    def test_to_markdown_failed(self):
        """Failed tasks render with bang and error info."""
        task = DesignTask("Bad step", 0)
        task.status = TaskStatus.FAILED
        task.result = "Geometry error"
        assert task.to_markdown() == "[!] Bad step (FAILED: Geometry error)"

    def test_to_markdown_skipped(self):
        """Skipped tasks render with tilde."""
        task = DesignTask("Skip step", 0)
        task.status = TaskStatus.SKIPPED
        assert task.to_markdown() == "[~] Skip step (skipped)"


class TestTaskManager:
    """Tests for the TaskManager class."""

    def test_initial_state(self):
        """A new TaskManager has no plan and is not complete."""
        mgr = TaskManager()
        assert mgr.has_plan is False
        assert mgr.is_complete is False
        assert mgr.current_step == -1
        assert mgr.progress == {
            "total": 0, "completed": 0, "in_progress": 0,
            "pending": 0, "failed": 0,
        }

    def test_create_plan(self):
        """Creating a plan produces the expected tasks."""
        mgr = TaskManager()
        steps = ["Step 1", "Step 2", "Step 3"]
        tasks = mgr.create_plan("Test Plan", steps)
        assert len(tasks) == 3
        assert mgr.has_plan is True
        assert mgr.progress["total"] == 3
        assert mgr.progress["pending"] == 3

    def test_start_step_default(self):
        """start_step() without an index starts the first pending step."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A", "B", "C"])
        task = mgr.start_step()
        assert task.index == 0
        assert task.status == TaskStatus.IN_PROGRESS
        assert mgr.current_step == 0

    def test_start_step_by_index(self):
        """start_step(index) starts the specified step."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A", "B", "C"])
        task = mgr.start_step(1)
        assert task.index == 1
        assert task.status == TaskStatus.IN_PROGRESS
        assert mgr.current_step == 1

    def test_complete_step(self):
        """complete_step marks a step as completed with a result."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A", "B"])
        mgr.start_step(0)
        task = mgr.complete_step(0, "Done successfully")
        assert task.status == TaskStatus.COMPLETED
        assert task.result == "Done successfully"
        assert task.completed_at != ""

    def test_fail_step(self):
        """fail_step marks a step as failed with an error."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A", "B"])
        task = mgr.fail_step(0, "Geometry error")
        assert task.status == TaskStatus.FAILED
        assert task.result == "Geometry error"
        assert task.completed_at != ""

    def test_skip_step(self):
        """skip_step marks a step as skipped."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A", "B"])
        task = mgr.skip_step(1)
        assert task.status == TaskStatus.SKIPPED

    def test_out_of_range_returns_none(self):
        """Operations on out-of-range indices return None."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A"])
        assert mgr.start_step(5) is None
        assert mgr.complete_step(5) is None
        assert mgr.fail_step(-1) is None
        assert mgr.skip_step(100) is None

    def test_is_complete(self):
        """is_complete is True only when all tasks have a terminal status."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A", "B", "C"])
        assert mgr.is_complete is False

        mgr.complete_step(0, "ok")
        assert mgr.is_complete is False

        mgr.skip_step(1)
        assert mgr.is_complete is False

        mgr.fail_step(2, "err")
        assert mgr.is_complete is True

    def test_progress_tracking(self):
        """Progress dict reflects the current step statuses."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A", "B", "C", "D"])
        mgr.complete_step(0)
        mgr.start_step(1)
        mgr.fail_step(2, "err")
        # D stays pending

        prog = mgr.progress
        assert prog["total"] == 4
        assert prog["completed"] == 1
        assert prog["in_progress"] == 1
        assert prog["failed"] == 1
        assert prog["pending"] == 1

    def test_clear(self):
        """clear() resets all state."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A", "B"])
        mgr.complete_step(0)

        mgr.clear()
        assert mgr.has_plan is False
        assert mgr.current_step == -1
        assert mgr.progress["total"] == 0

    def test_to_markdown(self):
        """to_markdown renders the full plan."""
        mgr = TaskManager()
        mgr.create_plan("Coffee Mug", ["Create body", "Add handle"])
        mgr.complete_step(0, "ok")

        md = mgr.to_markdown()
        assert "## Design Plan: Coffee Mug" in md
        assert "[x] Create body" in md
        assert "[ ] Add handle" in md
        assert "1/2 steps complete" in md

    def test_to_markdown_empty(self):
        """to_markdown returns empty string when no plan exists."""
        mgr = TaskManager()
        assert mgr.to_markdown() == ""

    def test_to_dict(self):
        """to_dict produces a complete serialisation."""
        mgr = TaskManager()
        mgr.create_plan("Test", ["A"])
        d = mgr.to_dict()
        assert d["title"] == "Test"
        assert len(d["tasks"]) == 1
        assert d["current_step"] == -1
        assert d["is_complete"] is False
        assert "progress" in d

    def test_get_context_injection_empty(self):
        """get_context_injection returns empty when no plan exists."""
        mgr = TaskManager()
        assert mgr.get_context_injection() == ""

    def test_get_context_injection_with_plan(self):
        """get_context_injection returns markdown with delimiters."""
        mgr = TaskManager()
        mgr.create_plan("Test", ["Step 1"])
        ctx = mgr.get_context_injection()
        assert ctx.startswith("\n\n---\n")
        assert ctx.endswith("\n---\n")
        assert "Design Plan: Test" in ctx

    def test_start_step_no_pending_returns_none(self):
        """start_step() with no pending steps returns None."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A"])
        mgr.complete_step(0)
        assert mgr.start_step() is None

    def test_create_plan_replaces_previous(self):
        """Creating a new plan replaces the old one entirely."""
        mgr = TaskManager()
        mgr.create_plan("Old", ["X", "Y"])
        mgr.complete_step(0)

        mgr.create_plan("New", ["A", "B", "C"])
        assert mgr.progress["total"] == 3
        assert mgr.progress["completed"] == 0
        assert mgr.current_step == -1

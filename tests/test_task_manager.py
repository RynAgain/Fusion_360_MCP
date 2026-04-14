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


# ======================================================================
# Phase 1a -- Orchestration extensions
# ======================================================================


class TestDesignTaskOrchestration:
    """Tests for the new orchestration fields on DesignTask."""

    def test_new_fields_default_values(self):
        """New fields have safe defaults when not provided."""
        task = DesignTask("Plain step", 0)
        assert task.mode_hint is None
        assert task.depends_on == []
        assert task.subtask_result is None
        assert task.retry_count == 0
        assert task.max_retries == 2

    def test_new_fields_explicit(self):
        """New fields can be set via constructor kwargs."""
        task = DesignTask(
            "Sketch circle",
            1,
            mode_hint="sketch",
            depends_on=[0],
            subtask_result="circle created",
            retry_count=1,
            max_retries=3,
        )
        assert task.mode_hint == "sketch"
        assert task.depends_on == [0]
        assert task.subtask_result == "circle created"
        assert task.retry_count == 1
        assert task.max_retries == 3

    def test_to_dict_includes_new_fields(self):
        """to_dict serialises all orchestration fields."""
        task = DesignTask("Extrude", 2, mode_hint="modeling", depends_on=[0, 1])
        d = task.to_dict()
        assert d["mode_hint"] == "modeling"
        assert d["depends_on"] == [0, 1]
        assert d["subtask_result"] is None
        assert d["retry_count"] == 0
        assert d["max_retries"] == 2

    def test_to_markdown_with_mode_hint(self):
        """Markdown output includes mode hint annotation."""
        task = DesignTask("Create sketch", 0, mode_hint="sketch")
        assert task.to_markdown() == "[ ] Create sketch (mode: sketch)"

    def test_to_markdown_with_depends_on(self):
        """Markdown output includes 1-based dependency references."""
        task = DesignTask("Extrude", 1, depends_on=[0])
        assert task.to_markdown() == "[ ] Extrude [depends on: 1]"

    def test_to_markdown_with_mode_and_depends(self):
        """Markdown output includes both mode hint and dependencies."""
        task = DesignTask("Analyse", 2, mode_hint="analysis", depends_on=[0, 1])
        md = task.to_markdown()
        assert "(mode: analysis)" in md
        assert "[depends on: 1, 2]" in md

    def test_to_markdown_completed_with_mode(self):
        """Completed step still shows mode annotation."""
        task = DesignTask("Done step", 0, mode_hint="sketch")
        task.status = TaskStatus.COMPLETED
        assert task.to_markdown() == "[x] Done step (mode: sketch)"

    def test_to_markdown_failed_with_mode(self):
        """Failed step shows mode + failure info."""
        task = DesignTask("Bad step", 0, mode_hint="modeling")
        task.status = TaskStatus.FAILED
        task.result = "crash"
        md = task.to_markdown()
        assert "(mode: modeling)" in md
        assert "(FAILED: crash)" in md


class TestCreateOrchestratedPlan:
    """Tests for TaskManager.create_orchestrated_plan()."""

    def test_basic_creation(self):
        """Orchestrated plan creates tasks with mode hints and deps."""
        mgr = TaskManager()
        steps = [
            {"description": "Create base sketch", "mode_hint": "sketch"},
            {"description": "Extrude base", "mode_hint": "modeling", "depends_on": [0]},
            {"description": "Run stress analysis", "mode_hint": "analysis", "depends_on": [1]},
        ]
        tasks = mgr.create_orchestrated_plan("Gear Assembly", steps)
        assert len(tasks) == 3
        assert mgr.has_plan is True
        assert tasks[0].mode_hint == "sketch"
        assert tasks[0].depends_on == []
        assert tasks[1].mode_hint == "modeling"
        assert tasks[1].depends_on == [0]
        assert tasks[2].depends_on == [1]

    def test_no_mode_hint(self):
        """Steps without mode_hint default to None."""
        mgr = TaskManager()
        tasks = mgr.create_orchestrated_plan("Simple", [{"description": "Do thing"}])
        assert tasks[0].mode_hint is None
        assert tasks[0].depends_on == []

    def test_replaces_previous_plan(self):
        """Orchestrated plan replaces any existing plan."""
        mgr = TaskManager()
        mgr.create_plan("Old", ["X"])
        mgr.create_orchestrated_plan("New", [{"description": "A"}, {"description": "B"}])
        assert mgr.progress["total"] == 2
        assert mgr.current_step == -1


class TestGetReadySteps:
    """Tests for TaskManager.get_ready_steps()."""

    def _make_mgr(self):
        """Helper: create a 3-step linear dependency chain."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Chain", [
            {"description": "Step A"},
            {"description": "Step B", "depends_on": [0]},
            {"description": "Step C", "depends_on": [1]},
        ])
        return mgr

    def test_initial_ready(self):
        """Only the root step (no deps) is ready initially."""
        mgr = self._make_mgr()
        ready = mgr.get_ready_steps()
        assert len(ready) == 1
        assert ready[0].index == 0

    def test_after_completing_first(self):
        """Completing step 0 makes step 1 ready."""
        mgr = self._make_mgr()
        mgr.complete_step(0, "ok")
        ready = mgr.get_ready_steps()
        assert len(ready) == 1
        assert ready[0].index == 1

    def test_skipped_satisfies_dependency(self):
        """Skipping a step counts as satisfying a dependency."""
        mgr = self._make_mgr()
        mgr.skip_step(0)
        ready = mgr.get_ready_steps()
        assert any(t.index == 1 for t in ready)

    def test_failed_blocks_dependency(self):
        """A failed dependency blocks downstream steps."""
        mgr = self._make_mgr()
        mgr.fail_step(0, "err")
        ready = mgr.get_ready_steps()
        # step 0 is failed (not pending), step 1 blocked, step 2 blocked
        assert len(ready) == 0

    def test_parallel_ready_steps(self):
        """Multiple steps with no deps are all ready simultaneously."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Parallel", [
            {"description": "A"},
            {"description": "B"},
            {"description": "C", "depends_on": [0, 1]},
        ])
        ready = mgr.get_ready_steps()
        assert len(ready) == 2
        assert ready[0].index == 0
        assert ready[1].index == 1

    def test_empty_plan(self):
        """Empty plan has no ready steps."""
        mgr = TaskManager()
        assert mgr.get_ready_steps() == []

    def test_all_completed(self):
        """All-completed plan has no ready steps."""
        mgr = self._make_mgr()
        mgr.complete_step(0)
        mgr.complete_step(1)
        mgr.complete_step(2)
        assert mgr.get_ready_steps() == []

    def test_in_progress_not_ready(self):
        """In-progress steps are not returned as ready."""
        mgr = self._make_mgr()
        mgr.start_step(0)
        ready = mgr.get_ready_steps()
        # step 0 is in_progress, not pending
        assert len(ready) == 0

    def test_forward_dependency_never_ready(self):
        """A step depending on a later step never becomes ready if that step stays pending."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Circular-ish", [
            {"description": "A", "depends_on": [1]},
            {"description": "B"},
        ])
        ready = mgr.get_ready_steps()
        # Only B (index 1) is ready; A depends on B which is pending
        assert len(ready) == 1
        assert ready[0].index == 1


class TestAutoAdvance:
    """Tests for TaskManager.auto_advance()."""

    def test_returns_first_ready(self):
        """auto_advance returns the lowest-index ready step."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Test", [
            {"description": "A"},
            {"description": "B"},
        ])
        step = mgr.auto_advance()
        assert step is not None
        assert step.index == 0

    def test_returns_none_when_complete(self):
        """auto_advance returns None when plan is complete."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Test", [{"description": "A"}])
        mgr.complete_step(0)
        assert mgr.auto_advance() is None

    def test_returns_none_when_blocked(self):
        """auto_advance returns None when all pending steps are blocked."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Test", [
            {"description": "A"},
            {"description": "B", "depends_on": [0]},
        ])
        mgr.fail_step(0, "err")
        assert mgr.auto_advance() is None

    def test_does_not_change_status(self):
        """auto_advance does not modify the step's status."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Test", [{"description": "A"}])
        step = mgr.auto_advance()
        assert step.status == TaskStatus.PENDING

    def test_empty_plan(self):
        """auto_advance on empty plan returns None."""
        mgr = TaskManager()
        assert mgr.auto_advance() is None


class TestRetry:
    """Tests for can_retry() and retry_step()."""

    def test_can_retry_failed_step(self):
        """can_retry returns True for a failed step within retry limit."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A"])
        mgr.fail_step(0, "err")
        assert mgr.can_retry(0) is True

    def test_cannot_retry_non_failed(self):
        """can_retry returns False for a non-failed step."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A"])
        assert mgr.can_retry(0) is False  # PENDING, not FAILED

    def test_cannot_retry_exhausted(self):
        """can_retry returns False after max retries reached."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A"])
        mgr.fail_step(0, "err")
        mgr.retry_step(0)
        mgr.fail_step(0, "err again")
        mgr.retry_step(0)
        mgr.fail_step(0, "err once more")
        # retry_count is now 2, max_retries is 2
        assert mgr.can_retry(0) is False

    def test_cannot_retry_invalid_index(self):
        """can_retry returns False for out-of-range index."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A"])
        assert mgr.can_retry(99) is False

    def test_retry_step_resets_to_pending(self):
        """retry_step resets status to PENDING and clears result."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A"])
        mgr.fail_step(0, "err")
        task = mgr.retry_step(0)
        assert task is not None
        assert task.status == TaskStatus.PENDING
        assert task.result == ""
        assert task.completed_at == ""
        assert task.retry_count == 1

    def test_retry_step_returns_none_when_exhausted(self):
        """retry_step returns None when retries are exhausted."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A"])
        mgr.fail_step(0, "err")
        mgr.retry_step(0)  # retry_count -> 1
        mgr.fail_step(0, "err")
        mgr.retry_step(0)  # retry_count -> 2
        mgr.fail_step(0, "err")
        assert mgr.retry_step(0) is None

    def test_retry_step_invalid_index(self):
        """retry_step returns None for out-of-range index."""
        mgr = TaskManager()
        assert mgr.retry_step(5) is None


class TestDependencyGraph:
    """Tests for TaskManager.get_dependency_graph()."""

    def test_linear_chain(self):
        """Linear chain produces correct graph."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Chain", [
            {"description": "A"},
            {"description": "B", "depends_on": [0]},
            {"description": "C", "depends_on": [1]},
        ])
        graph = mgr.get_dependency_graph()
        assert graph == {0: [], 1: [0], 2: [1]}

    def test_simple_plan_empty_deps(self):
        """Plain create_plan produces empty dependency lists."""
        mgr = TaskManager()
        mgr.create_plan("Simple", ["A", "B"])
        graph = mgr.get_dependency_graph()
        assert graph == {0: [], 1: []}

    def test_empty_plan(self):
        """Empty plan produces empty graph."""
        mgr = TaskManager()
        assert mgr.get_dependency_graph() == {}


class TestPlanSummary:
    """Tests for TaskManager.get_plan_summary()."""

    def test_full_summary(self):
        """get_plan_summary returns all expected keys and values."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Gear", [
            {"description": "A"},
            {"description": "B", "depends_on": [0]},
            {"description": "C", "depends_on": [1]},
        ])
        mgr.complete_step(0, "done")
        summary = mgr.get_plan_summary()
        assert summary["title"] == "Gear"
        assert summary["total_steps"] == 3
        assert summary["completed"] == 1
        assert summary["failed"] == 0
        assert summary["in_progress"] == 0
        assert summary["pending"] == 2
        assert summary["ready"] == 1  # step B is ready
        assert summary["is_complete"] is False
        assert summary["has_failures"] is False

    def test_summary_with_failures(self):
        """has_failures is True when any step has failed."""
        mgr = TaskManager()
        mgr.create_plan("Plan", ["A", "B"])
        mgr.fail_step(0, "err")
        summary = mgr.get_plan_summary()
        assert summary["has_failures"] is True
        assert summary["failed"] == 1

    def test_summary_empty_plan(self):
        """Summary for empty plan has zero counts."""
        mgr = TaskManager()
        summary = mgr.get_plan_summary()
        assert summary["total_steps"] == 0
        assert summary["ready"] == 0
        assert summary["is_complete"] is False


class TestContextInjectionOrchestrated:
    """Tests for updated get_context_injection with orchestration info."""

    def test_mode_hints_in_context(self):
        """Context injection includes mode hint annotations."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Gear Assembly", [
            {"description": "Create base sketch", "mode_hint": "sketch"},
            {"description": "Extrude base", "mode_hint": "modeling", "depends_on": [0]},
        ])
        ctx = mgr.get_context_injection()
        assert "(mode: sketch)" in ctx
        assert "(mode: modeling)" in ctx

    def test_dependencies_in_context(self):
        """Context injection includes dependency annotations."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Test", [
            {"description": "A"},
            {"description": "B", "depends_on": [0]},
        ])
        ctx = mgr.get_context_injection()
        assert "[depends on: 1]" in ctx

    def test_ready_steps_in_context(self):
        """Context injection includes ready step hints."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Test", [
            {"description": "A"},
            {"description": "B", "depends_on": [0]},
        ])
        ctx = mgr.get_context_injection()
        assert "Ready steps: Step 1" in ctx

    def test_ready_steps_after_completion(self):
        """Ready steps update after completing a dependency."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Test", [
            {"description": "A"},
            {"description": "B", "depends_on": [0]},
        ])
        mgr.complete_step(0)
        ctx = mgr.get_context_injection()
        assert "Ready steps: Step 2" in ctx

    def test_no_ready_line_when_all_complete(self):
        """No ready steps line when plan is fully complete."""
        mgr = TaskManager()
        mgr.create_plan("Test", ["A"])
        mgr.complete_step(0)
        md = mgr.to_markdown()
        assert "Ready steps:" not in md

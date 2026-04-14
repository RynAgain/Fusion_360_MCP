"""
tests/test_context_bridge.py
Tests for the context bridge that assembles subtask context packets.
"""

import pytest

from ai.context_bridge import ContextBridge, SubtaskContext
from ai.task_manager import TaskManager


# ======================================================================
# Helpers -- lightweight fakes for duck-typed dependencies
# ======================================================================


class FakeDesignStateTracker:
    """Minimal duck-typed stand-in for DesignStateTracker."""

    def __init__(self, summary: str = "Design State: 2 bodies, timeline pos 5"):
        self._summary = summary

    def to_summary_string(self) -> str:
        return self._summary


class BrokenDesignStateTracker:
    """Tracker that raises on summary access (tests graceful degradation)."""

    def to_summary_string(self) -> str:
        raise RuntimeError("Fusion 360 disconnected")


# ======================================================================
# SubtaskContext tests
# ======================================================================


class TestSubtaskContext:
    """Tests for the SubtaskContext dataclass."""

    def _make_minimal(self) -> SubtaskContext:
        """Return a SubtaskContext with only required fields."""
        return SubtaskContext(
            step_index=0,
            step_description="Create base sketch",
            mode="sketch",
            plan_title="Gear Assembly",
            plan_summary="## Design Plan: Gear Assembly\n[ ] Create base sketch",
        )

    def test_to_system_context_basic(self):
        """Renders correctly with minimal data (no deps, no state)."""
        ctx = self._make_minimal()
        text = ctx.to_system_context()

        assert "## Orchestrated Subtask" in text
        assert "**Plan:** Gear Assembly" in text
        assert "**Current Step:** Step 1: Create base sketch" in text
        assert "**Target Mode:** sketch" in text
        assert "### Plan Overview" in text
        assert "### Important" in text
        # Should NOT contain optional sections when empty
        assert "### Dependency Results" not in text
        assert "### Current Design State" not in text
        assert "### Instructions" not in text

    def test_to_system_context_with_dependencies(self):
        """Includes dependency results when provided."""
        ctx = SubtaskContext(
            step_index=1,
            step_description="Extrude base",
            mode="modeling",
            plan_title="Gear Assembly",
            plan_summary="## Design Plan: Gear Assembly",
            dependency_results=[
                {
                    "index": 0,
                    "description": "Create base sketch",
                    "result": "Created a 50mm circle on the XY plane",
                    "mode": "sketch",
                },
            ],
        )
        text = ctx.to_system_context()

        assert "### Dependency Results" in text
        assert "**Step 1: Create base sketch**" in text
        assert "Created a 50mm circle on the XY plane" in text

    def test_to_system_context_with_design_state(self):
        """Includes design state when provided."""
        ctx = SubtaskContext(
            step_index=0,
            step_description="Add fillets",
            mode="modeling",
            plan_title="Box",
            plan_summary="## Design Plan: Box",
            design_state_summary="Design State: 1 body [Box (vol=100cm3, 6 faces)]",
        )
        text = ctx.to_system_context()

        assert "### Current Design State" in text
        assert "1 body" in text
        assert "vol=100cm3" in text

    def test_to_system_context_with_instructions(self):
        """Includes instructions when provided."""
        ctx = SubtaskContext(
            step_index=0,
            step_description="Sketch circle",
            mode="sketch",
            plan_title="Test",
            plan_summary="## Design Plan: Test",
            instructions="Use a 25mm radius centered at origin.",
        )
        text = ctx.to_system_context()

        assert "### Instructions" in text
        assert "Use a 25mm radius centered at origin." in text

    def test_to_system_context_full(self):
        """All sections render when all fields are populated."""
        ctx = SubtaskContext(
            step_index=2,
            step_description="Run analysis",
            mode="analysis",
            plan_title="Full Test",
            plan_summary="## Design Plan: Full Test\n[x] Step A\n[x] Step B\n[ ] Run analysis",
            dependency_results=[
                {"index": 0, "description": "Step A", "result": "Done A", "mode": "sketch"},
                {"index": 1, "description": "Step B", "result": "Done B", "mode": "modeling"},
            ],
            design_state_summary="Design State: 3 bodies",
            instructions="Focus on stress points.",
            estimated_tokens=500,
        )
        text = ctx.to_system_context()

        assert "### Plan Overview" in text
        assert "### Dependency Results" in text
        assert "### Current Design State" in text
        assert "### Instructions" in text
        assert "### Important" in text
        assert "Step 3: Run analysis" in text

    def test_to_dict_roundtrip(self):
        """Serialization produces all expected keys with correct values."""
        ctx = SubtaskContext(
            step_index=1,
            step_description="Extrude",
            mode="modeling",
            plan_title="Test Plan",
            plan_summary="summary text",
            dependency_results=[{"index": 0, "description": "Sketch", "result": "ok"}],
            design_state_summary="Design State: 1 body",
            instructions="Be careful",
            estimated_tokens=42,
        )
        d = ctx.to_dict()

        assert d["step_index"] == 1
        assert d["step_description"] == "Extrude"
        assert d["mode"] == "modeling"
        assert d["plan_title"] == "Test Plan"
        assert d["plan_summary"] == "summary text"
        assert len(d["dependency_results"]) == 1
        assert d["dependency_results"][0]["description"] == "Sketch"
        assert d["design_state_summary"] == "Design State: 1 body"
        assert d["instructions"] == "Be careful"
        assert d["estimated_tokens"] == 42

    def test_to_dict_does_not_share_references(self):
        """to_dict returns a copy of dependency_results, not a reference."""
        deps = [{"index": 0, "description": "A", "result": "ok"}]
        ctx = SubtaskContext(
            step_index=0,
            step_description="X",
            mode="full",
            plan_title="T",
            plan_summary="S",
            dependency_results=deps,
        )
        d = ctx.to_dict()
        d["dependency_results"].append({"index": 99})
        # Original should be unaffected
        assert len(ctx.dependency_results) == 1

    def test_to_system_context_multiple_dependencies(self):
        """Multiple dependency results all appear in order."""
        deps = [
            {"index": 0, "description": "Step A", "result": "Result A"},
            {"index": 1, "description": "Step B", "result": "Result B"},
            {"index": 2, "description": "Step C", "result": "Result C"},
        ]
        ctx = SubtaskContext(
            step_index=3,
            step_description="Final",
            mode="full",
            plan_title="Multi",
            plan_summary="plan",
            dependency_results=deps,
        )
        text = ctx.to_system_context()
        # All three should be present
        assert "Step 1: Step A" in text
        assert "Step 2: Step B" in text
        assert "Step 3: Step C" in text
        assert "Result A" in text
        assert "Result B" in text
        assert "Result C" in text


# ======================================================================
# ContextBridge tests
# ======================================================================


class TestContextBridgeRecording:
    """Tests for recording and retrieving subtask results."""

    def test_record_subtask_result(self):
        """Records and retrieves results correctly."""
        bridge = ContextBridge()
        bridge.record_subtask_result(0, "Create sketch", "Circle drawn", "sketch")

        results = bridge.recorded_results
        assert 0 in results
        assert results[0]["description"] == "Create sketch"
        assert results[0]["result"] == "Circle drawn"
        assert results[0]["mode"] == "sketch"
        assert "completed_at" in results[0]

    def test_record_multiple_results(self):
        """Multiple results are stored independently."""
        bridge = ContextBridge()
        bridge.record_subtask_result(0, "Step A", "Result A", "sketch")
        bridge.record_subtask_result(1, "Step B", "Result B", "modeling")

        results = bridge.recorded_results
        assert len(results) == 2
        assert results[0]["result"] == "Result A"
        assert results[1]["result"] == "Result B"

    def test_record_overwrites_same_index(self):
        """Recording for the same step_index overwrites the previous entry."""
        bridge = ContextBridge()
        bridge.record_subtask_result(0, "Step A", "First attempt", "sketch")
        bridge.record_subtask_result(0, "Step A", "Second attempt", "sketch")

        results = bridge.recorded_results
        assert len(results) == 1
        assert results[0]["result"] == "Second attempt"

    def test_recorded_results_is_copy(self):
        """recorded_results returns a copy, not the internal dict."""
        bridge = ContextBridge()
        bridge.record_subtask_result(0, "Step A", "ok", "sketch")
        results = bridge.recorded_results
        results[99] = {"fake": True}
        # Internal state should be unaffected
        assert 99 not in bridge.recorded_results


class TestContextBridgeBuildContext:
    """Tests for ContextBridge.build_context()."""

    def _make_plan(self) -> TaskManager:
        """Create a 3-step orchestrated plan."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan(
            "Gear Assembly",
            [
                {"description": "Create base sketch", "mode_hint": "sketch"},
                {
                    "description": "Extrude base",
                    "mode_hint": "modeling",
                    "depends_on": [0],
                },
                {
                    "description": "Run stress analysis",
                    "mode_hint": "analysis",
                    "depends_on": [1],
                },
            ],
        )
        return mgr

    def test_build_context_auto_advance(self):
        """Uses auto_advance when no step_index given."""
        mgr = self._make_plan()
        bridge = ContextBridge()

        ctx = bridge.build_context(mgr)
        assert ctx.step_index == 0
        assert ctx.step_description == "Create base sketch"
        assert ctx.mode == "sketch"
        assert ctx.plan_title == "Gear Assembly"

    def test_build_context_specific_step(self):
        """Builds context for a specific step by index."""
        mgr = self._make_plan()
        bridge = ContextBridge()

        ctx = bridge.build_context(mgr, step_index=1)
        assert ctx.step_index == 1
        assert ctx.step_description == "Extrude base"
        assert ctx.mode == "modeling"

    def test_build_context_with_dependencies(self):
        """Dependency results are included when available."""
        mgr = self._make_plan()
        mgr.complete_step(0, "Sketch created")
        bridge = ContextBridge()
        bridge.record_subtask_result(
            0, "Create base sketch", "50mm circle on XY plane", "sketch"
        )

        ctx = bridge.build_context(mgr, step_index=1)
        assert len(ctx.dependency_results) == 1
        assert ctx.dependency_results[0]["index"] == 0
        assert ctx.dependency_results[0]["result"] == "50mm circle on XY plane"

    def test_build_context_no_plan(self):
        """Raises ValueError when no plan exists (auto_advance returns None)."""
        mgr = TaskManager()
        bridge = ContextBridge()

        with pytest.raises(ValueError, match="No step available"):
            bridge.build_context(mgr)

    def test_build_context_invalid_step_index(self):
        """Raises ValueError for out-of-range step_index."""
        mgr = self._make_plan()
        bridge = ContextBridge()

        with pytest.raises(ValueError, match="Invalid step_index"):
            bridge.build_context(mgr, step_index=99)

    def test_build_context_negative_step_index(self):
        """Raises ValueError for negative step_index."""
        mgr = self._make_plan()
        bridge = ContextBridge()

        with pytest.raises(ValueError, match="Invalid step_index"):
            bridge.build_context(mgr, step_index=-1)

    def test_build_context_mode_fallback(self):
        """Falls back to 'full' when step has no mode_hint."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan("Simple", [{"description": "Do thing"}])
        bridge = ContextBridge()

        ctx = bridge.build_context(mgr, step_index=0)
        assert ctx.mode == "full"

    def test_build_context_with_design_state(self):
        """Design state is included when tracker is provided."""
        mgr = self._make_plan()
        tracker = FakeDesignStateTracker("Design State: 2 bodies, timeline pos 5")
        bridge = ContextBridge()

        ctx = bridge.build_context(mgr, design_state_tracker=tracker)
        assert ctx.design_state_summary == "Design State: 2 bodies, timeline pos 5"

    def test_build_context_design_state_failure(self):
        """Gracefully handles design state tracker failure."""
        mgr = self._make_plan()
        tracker = BrokenDesignStateTracker()
        bridge = ContextBridge()

        # Should not raise; design_state_summary should be None
        ctx = bridge.build_context(mgr, design_state_tracker=tracker)
        assert ctx.design_state_summary is None

    def test_build_context_additional_instructions(self):
        """Additional instructions are passed through."""
        mgr = self._make_plan()
        bridge = ContextBridge()

        ctx = bridge.build_context(
            mgr, additional_instructions="Use metric units only"
        )
        assert ctx.instructions == "Use metric units only"

    def test_build_context_plan_summary_present(self):
        """Plan summary is the markdown rendering of the plan."""
        mgr = self._make_plan()
        bridge = ContextBridge()

        ctx = bridge.build_context(mgr)
        assert "## Design Plan: Gear Assembly" in ctx.plan_summary

    def test_build_context_estimates_tokens(self):
        """Built context has a non-zero token estimate."""
        mgr = self._make_plan()
        bridge = ContextBridge()

        ctx = bridge.build_context(mgr)
        assert ctx.estimated_tokens > 0

    def test_build_context_all_steps_complete(self):
        """Raises ValueError when plan is complete (auto_advance returns None)."""
        mgr = self._make_plan()
        mgr.complete_step(0)
        mgr.complete_step(1)
        mgr.complete_step(2)
        bridge = ContextBridge()

        with pytest.raises(ValueError, match="No step available"):
            bridge.build_context(mgr)


class TestGetDependencyResults:
    """Tests for ContextBridge.get_dependency_results()."""

    def _make_plan(self) -> TaskManager:
        """Create a plan with dependencies."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan(
            "Test",
            [
                {"description": "Step A"},
                {"description": "Step B", "depends_on": [0]},
                {"description": "Step C", "depends_on": [0, 1]},
            ],
        )
        return mgr

    def test_get_dependency_results(self):
        """Returns only completed dependency results."""
        mgr = self._make_plan()
        bridge = ContextBridge()
        bridge.record_subtask_result(0, "Step A", "Result A", "sketch")

        results = bridge.get_dependency_results(1, mgr)
        assert len(results) == 1
        assert results[0]["index"] == 0
        assert results[0]["result"] == "Result A"

    def test_get_dependency_results_partial(self):
        """Some deps completed, some not -- only completed are returned."""
        mgr = self._make_plan()
        bridge = ContextBridge()
        bridge.record_subtask_result(0, "Step A", "Result A", "sketch")
        # Step B not recorded

        results = bridge.get_dependency_results(2, mgr)
        assert len(results) == 1
        assert results[0]["index"] == 0

    def test_get_dependency_results_none_completed(self):
        """No deps completed -- returns empty list."""
        mgr = self._make_plan()
        bridge = ContextBridge()

        results = bridge.get_dependency_results(2, mgr)
        assert results == []

    def test_get_dependency_results_no_deps(self):
        """Step with no dependencies returns empty list."""
        mgr = self._make_plan()
        bridge = ContextBridge()
        bridge.record_subtask_result(0, "Step A", "Result A", "sketch")

        results = bridge.get_dependency_results(0, mgr)
        assert results == []

    def test_get_dependency_results_invalid_index(self):
        """Invalid step_index returns empty list."""
        mgr = self._make_plan()
        bridge = ContextBridge()

        results = bridge.get_dependency_results(99, mgr)
        assert results == []

    def test_get_dependency_results_all_completed(self):
        """All deps completed -- all returned."""
        mgr = self._make_plan()
        bridge = ContextBridge()
        bridge.record_subtask_result(0, "Step A", "Result A", "sketch")
        bridge.record_subtask_result(1, "Step B", "Result B", "modeling")

        results = bridge.get_dependency_results(2, mgr)
        assert len(results) == 2
        assert results[0]["index"] == 0
        assert results[1]["index"] == 1


class TestContextBridgeClear:
    """Tests for ContextBridge.clear()."""

    def test_clear(self):
        """Clears all results."""
        bridge = ContextBridge()
        bridge.record_subtask_result(0, "Step A", "ok", "sketch")
        bridge.record_subtask_result(1, "Step B", "ok", "modeling")

        bridge.clear()
        assert bridge.recorded_results == {}

    def test_clear_idempotent(self):
        """Clearing an already-empty bridge is safe."""
        bridge = ContextBridge()
        bridge.clear()
        assert bridge.recorded_results == {}


class TestGetResultsSummary:
    """Tests for ContextBridge.get_results_summary()."""

    def test_get_results_summary(self):
        """Markdown summary is correct."""
        bridge = ContextBridge()
        bridge.record_subtask_result(0, "Create sketch", "Circle drawn", "sketch")
        bridge.record_subtask_result(1, "Extrude", "Box created", "modeling")

        summary = bridge.get_results_summary()
        assert "## Subtask Results" in summary
        assert "**Step 1** (sketch): Create sketch" in summary
        assert "Result: Circle drawn" in summary
        assert "**Step 2** (modeling): Extrude" in summary
        assert "Result: Box created" in summary

    def test_get_results_summary_empty(self):
        """Empty bridge returns a no-results message."""
        bridge = ContextBridge()
        summary = bridge.get_results_summary()
        assert "No subtask results recorded" in summary

    def test_get_results_summary_ordering(self):
        """Results are sorted by step index regardless of insertion order."""
        bridge = ContextBridge()
        bridge.record_subtask_result(2, "Step C", "Result C", "analysis")
        bridge.record_subtask_result(0, "Step A", "Result A", "sketch")
        bridge.record_subtask_result(1, "Step B", "Result B", "modeling")

        summary = bridge.get_results_summary()
        # Step 1 should appear before Step 2 which should appear before Step 3
        idx_a = summary.index("Step 1")
        idx_b = summary.index("Step 2")
        idx_c = summary.index("Step 3")
        assert idx_a < idx_b < idx_c


class TestTruncateToBudget:
    """Tests for token budget enforcement."""

    def test_truncate_to_budget(self):
        """Context is truncated when over budget."""
        # Use a very small budget to force truncation
        bridge = ContextBridge(token_budget=50)

        mgr = TaskManager()
        mgr.create_orchestrated_plan(
            "Big Plan",
            [
                {"description": "Step A"},
                {"description": "Step B", "depends_on": [0]},
            ],
        )
        mgr.complete_step(0, "done")

        # Record a long result
        bridge.record_subtask_result(
            0, "Step A", "A" * 1000, "sketch"
        )

        ctx = bridge.build_context(mgr, step_index=1)
        # The context should have been truncated to fit within budget
        # (or as close as possible)
        assert ctx.estimated_tokens <= bridge._token_budget or len(ctx.dependency_results) == 0

    def test_within_budget_no_truncation(self):
        """Context within budget is not truncated."""
        bridge = ContextBridge(token_budget=100_000)

        mgr = TaskManager()
        mgr.create_orchestrated_plan(
            "Small Plan",
            [{"description": "Small step"}],
        )

        ctx = bridge.build_context(mgr)
        assert ctx.estimated_tokens < bridge._token_budget
        assert ctx.step_description == "Small step"

    def test_truncate_removes_oldest_deps_first(self):
        """When truncating, oldest dependency results are removed first."""
        bridge = ContextBridge(token_budget=100)

        mgr = TaskManager()
        mgr.create_orchestrated_plan(
            "Test",
            [
                {"description": "A"},
                {"description": "B"},
                {"description": "C"},
                {"description": "D", "depends_on": [0, 1, 2]},
            ],
        )
        mgr.complete_step(0)
        mgr.complete_step(1)
        mgr.complete_step(2)

        bridge.record_subtask_result(0, "A", "X" * 500, "sketch")
        bridge.record_subtask_result(1, "B", "Y" * 500, "modeling")
        bridge.record_subtask_result(2, "C", "Z" * 500, "analysis")

        ctx = bridge.build_context(mgr, step_index=3)
        # With such a small budget, some deps should have been removed
        # If any remain, they should be the most recent ones
        if ctx.dependency_results:
            remaining_indices = [d["index"] for d in ctx.dependency_results]
            # Oldest (index 0) should be removed before newest (index 2)
            if 0 in remaining_indices:
                assert 2 in remaining_indices

    def test_token_estimation(self):
        """Token estimation uses ~4 chars per token heuristic."""
        bridge = ContextBridge()
        ctx = SubtaskContext(
            step_index=0,
            step_description="Test",
            mode="full",
            plan_title="T",
            plan_summary="S",
        )
        tokens = bridge._estimate_context_tokens(ctx)
        text_len = len(ctx.to_system_context())
        assert tokens == text_len // 4


class TestContextBridgeIntegration:
    """Integration tests using real TaskManager objects."""

    def test_full_workflow(self):
        """Simulate a complete orchestrator workflow."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan(
            "Coffee Mug",
            [
                {"description": "Create mug body sketch", "mode_hint": "sketch"},
                {
                    "description": "Extrude and shell the mug",
                    "mode_hint": "modeling",
                    "depends_on": [0],
                },
                {
                    "description": "Add handle",
                    "mode_hint": "modeling",
                    "depends_on": [1],
                },
            ],
        )
        tracker = FakeDesignStateTracker("Design State: 0 bodies")
        bridge = ContextBridge()

        # -- Step 0: auto-advance picks the first ready step --
        ctx0 = bridge.build_context(mgr, design_state_tracker=tracker)
        assert ctx0.step_index == 0
        assert ctx0.mode == "sketch"
        assert ctx0.design_state_summary is not None
        assert len(ctx0.dependency_results) == 0

        # Verify system context is renderable
        text0 = ctx0.to_system_context()
        assert "Coffee Mug" in text0
        assert "Create mug body sketch" in text0

        # Simulate completing step 0
        mgr.complete_step(0, "Mug body sketch created")
        bridge.record_subtask_result(
            0, "Create mug body sketch", "Circle 80mm diameter on XY", "sketch"
        )

        # -- Step 1: auto-advance picks the next ready step --
        tracker_after = FakeDesignStateTracker("Design State: 0 bodies, 1 sketch")
        ctx1 = bridge.build_context(mgr, design_state_tracker=tracker_after)
        assert ctx1.step_index == 1
        assert ctx1.mode == "modeling"
        assert len(ctx1.dependency_results) == 1
        assert ctx1.dependency_results[0]["result"] == "Circle 80mm diameter on XY"

        # Simulate completing step 1
        mgr.complete_step(1, "Mug body extruded and shelled")
        bridge.record_subtask_result(
            1,
            "Extrude and shell the mug",
            "Body1 created: 80mm dia x 100mm tall, 2mm wall",
            "modeling",
        )

        # -- Step 2: depends on step 1 --
        tracker_final = FakeDesignStateTracker("Design State: 1 body [Body1]")
        ctx2 = bridge.build_context(mgr, design_state_tracker=tracker_final)
        assert ctx2.step_index == 2
        assert ctx2.mode == "modeling"
        assert len(ctx2.dependency_results) == 1
        assert ctx2.dependency_results[0]["index"] == 1

        # Complete the final step
        mgr.complete_step(2, "Handle added")
        bridge.record_subtask_result(2, "Add handle", "Handle swept along path", "modeling")

        # Verify summary
        summary = bridge.get_results_summary()
        assert "Step 1" in summary
        assert "Step 2" in summary
        assert "Step 3" in summary
        assert "Circle 80mm" in summary

    def test_clear_resets_for_new_plan(self):
        """After clearing, old results don't leak into new plan context."""
        mgr = TaskManager()
        mgr.create_orchestrated_plan(
            "Old Plan",
            [{"description": "Old step", "mode_hint": "sketch"}],
        )
        bridge = ContextBridge()
        bridge.record_subtask_result(0, "Old step", "Old result", "sketch")

        # Clear and start new plan
        bridge.clear()
        mgr.clear()
        mgr.create_orchestrated_plan(
            "New Plan",
            [
                {"description": "New step A"},
                {"description": "New step B", "depends_on": [0]},
            ],
        )

        ctx = bridge.build_context(mgr)
        assert ctx.step_description == "New step A"
        assert ctx.plan_title == "New Plan"
        assert len(ctx.dependency_results) == 0
        assert bridge.recorded_results == {}

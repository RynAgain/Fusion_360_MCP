"""
tests/test_design_state_tracker.py
Unit tests for ai/design_state_tracker.py -- persistent design state tracking,
delta computation, summary generation, and thread safety.
"""
import copy
import threading

import pytest
from unittest.mock import MagicMock

from ai.design_state_tracker import DesignStateTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mcp_with_bodies(bodies, component_count=1, timeline=None, sketches=None):
    """Return a mock MCP server that responds to execute_tool calls."""
    mcp = MagicMock()

    def execute_tool(name, params):
        if name == "get_body_list":
            return {
                "success": True,
                "bodies": bodies,
                "component_count": component_count,
            }
        if name == "get_body_properties":
            body_name = params.get("body_name", "")
            for b in bodies:
                if b.get("name") == body_name:
                    return {
                        "success": True,
                        "face_count": b.get("face_count", 6),
                        "volume": b.get("volume", 0),
                        "bounding_box": b.get("bounding_box"),
                    }
            return {"success": False, "error": "Body not found"}
        if name == "get_timeline":
            tl = timeline if timeline is not None else []
            return {"success": True, "timeline": tl}
        if name == "get_sketch_list":
            sk = sketches if sketches is not None else []
            return {"success": True, "sketches": sk}
        return {"success": False, "error": f"Unknown tool {name}"}

    mcp.execute_tool = MagicMock(side_effect=execute_tool)
    return mcp


def _make_failing_mcp():
    """Return a mock MCP server that raises on all calls."""
    mcp = MagicMock()
    mcp.execute_tool = MagicMock(side_effect=RuntimeError("MCP offline"))
    return mcp


# ---------------------------------------------------------------------------
# TestDesignStateTracker
# ---------------------------------------------------------------------------

class TestDesignStateTracker:
    """Comprehensive tests for DesignStateTracker."""

    def test_initial_state_empty(self):
        """After construction, to_dict() returns empty state."""
        tracker = DesignStateTracker()
        state = tracker.to_dict()
        assert state["bodies"] == []
        assert state["sketches"] == []
        assert state["timeline_position"] == 0
        assert state["component_count"] == 0

    def test_reset_clears_state(self):
        """After update, reset() returns to empty."""
        tracker = DesignStateTracker()
        bodies = [{"name": "Body1", "volume": 10.0}]
        mcp = _make_mcp_with_bodies(bodies)
        tracker.update(mcp)
        # State should be non-empty after update
        assert len(tracker.to_dict()["bodies"]) > 0
        # Reset
        tracker.reset()
        state = tracker.to_dict()
        assert state["bodies"] == []
        assert state["sketches"] == []
        assert state["timeline_position"] == 0
        assert state["component_count"] == 0

    def test_update_with_bodies(self):
        """Mock MCP returning body list with 2+ bodies and body properties."""
        bodies = [
            {"name": "Body1", "volume": 12.5, "face_count": 8, "bounding_box": {"min": [0, 0, 0], "max": [5, 5, 5]}},
            {"name": "Body2", "volume": 3.2, "face_count": 6, "bounding_box": {"min": [1, 1, 1], "max": [3, 3, 3]}},
        ]
        mcp = _make_mcp_with_bodies(bodies, component_count=2)
        tracker = DesignStateTracker()
        tracker.update(mcp)
        state = tracker.to_dict()
        assert len(state["bodies"]) == 2
        assert state["bodies"][0]["name"] == "Body1"
        assert state["bodies"][0]["volume"] == 12.5
        assert state["bodies"][0]["face_count"] == 8
        assert state["bodies"][1]["name"] == "Body2"
        assert state["bodies"][1]["volume"] == 3.2
        assert state["component_count"] == 2

    def test_update_with_sketches(self):
        """Mock MCP returning sketch list; verify sketch state."""
        sketches = [
            {"name": "Sketch1", "profile_count": 2},
            {"name": "Sketch2", "profile_count": 1},
        ]
        mcp = _make_mcp_with_bodies([], sketches=sketches)
        tracker = DesignStateTracker()
        tracker.update(mcp)
        state = tracker.to_dict()
        assert len(state["sketches"]) == 2
        assert state["sketches"][0]["name"] == "Sketch1"
        assert state["sketches"][0]["profile_count"] == 2
        assert state["sketches"][1]["name"] == "Sketch2"
        assert state["sketches"][1]["profile_count"] == 1

    def test_update_graceful_degradation(self):
        """Mock MCP that raises exceptions on all calls; verify update() doesn't crash."""
        mcp = _make_failing_mcp()
        tracker = DesignStateTracker()
        # Should not raise
        tracker.update(mcp)
        state = tracker.to_dict()
        # State should be valid (empty defaults)
        assert isinstance(state["bodies"], list)
        assert isinstance(state["sketches"], list)
        assert isinstance(state["timeline_position"], int)
        assert isinstance(state["component_count"], int)

    def test_update_with_various_statuses(self):
        """Mock MCP returning various status values; verify graceful handling."""
        mcp = MagicMock()

        def execute_tool(name, params):
            if name == "get_body_list":
                return {
                    "status": "success",
                    "success": True,
                    "bodies": [{"name": "SimBody", "volume": 1.0}],
                    "component_count": 1,
                }
            if name == "get_body_properties":
                return {
                    "status": "success",
                    "success": True,
                    "face_count": 6,
                    "volume": 1.0,
                    "bounding_box": None,
                }
            if name == "get_timeline":
                return {
                    "status": "success",
                    "success": True,
                    "timeline": [{"index": 0, "name": "Sketch1"}],
                }
            if name == "get_sketch_list":
                return {
                    "status": "success",
                    "success": True,
                    "sketches": [],
                }
            return {"success": False}

        mcp.execute_tool = MagicMock(side_effect=execute_tool)
        tracker = DesignStateTracker()
        tracker.update(mcp)
        state = tracker.to_dict()
        assert len(state["bodies"]) == 1
        assert state["bodies"][0]["name"] == "SimBody"
        assert state["timeline_position"] == 1

    def test_to_dict_deep_copy(self):
        """Verify that modifying the returned dict doesn't affect internal state."""
        bodies = [{"name": "Body1", "volume": 10.0}]
        mcp = _make_mcp_with_bodies(bodies)
        tracker = DesignStateTracker()
        tracker.update(mcp)
        state1 = tracker.to_dict()
        # Mutate the returned dict
        state1["bodies"].append({"name": "Injected", "volume": 999})
        state1["timeline_position"] = 999
        # Internal state should be unaffected
        state2 = tracker.to_dict()
        assert len(state2["bodies"]) == 1
        assert state2["timeline_position"] == 0  # no timeline entries -> 0

    def test_to_summary_string_with_bodies(self):
        """Verify format: 'Design State: N bodies [...], timeline pos X, N components'."""
        bodies = [
            {"name": "Body1", "volume": 12.5, "face_count": 8},
            {"name": "Base", "volume": 45.0, "face_count": 14},
        ]
        timeline = [{"index": 0}, {"index": 1}, {"index": 2}]
        mcp = _make_mcp_with_bodies(bodies, component_count=1, timeline=timeline)
        tracker = DesignStateTracker()
        tracker.update(mcp)
        summary = tracker.to_summary_string()
        assert summary.startswith("Design State:")
        assert "2 bodies" in summary
        assert "Body1" in summary
        assert "Base" in summary
        assert "vol=12.5cm3" in summary
        assert "8 faces" in summary
        assert "timeline pos 3" in summary
        assert "1 component" in summary

    def test_to_summary_string_empty(self):
        """Verify format for empty state."""
        tracker = DesignStateTracker()
        summary = tracker.to_summary_string()
        assert summary.startswith("Design State:")
        assert "0 bodies" in summary
        assert "timeline pos 0" in summary
        assert "0 components" in summary

    def test_get_delta_bodies_added(self):
        """Set state, update with new body, verify delta shows bodies_added."""
        tracker = DesignStateTracker()
        # Initial state: no bodies
        old_snapshot = tracker.to_dict()
        # Update with a body
        bodies = [{"name": "NewBody", "volume": 5.0}]
        mcp = _make_mcp_with_bodies(bodies)
        tracker.update(mcp)
        delta = tracker.get_delta(old_snapshot)
        assert len(delta["bodies_added"]) == 1
        assert delta["bodies_added"][0]["name"] == "NewBody"
        assert delta["bodies_removed"] == []

    def test_get_delta_bodies_removed(self):
        """Set state, update with body removed, verify delta shows bodies_removed."""
        tracker = DesignStateTracker()
        # Initial state: one body
        bodies = [{"name": "OldBody", "volume": 10.0}]
        mcp = _make_mcp_with_bodies(bodies)
        tracker.update(mcp)
        old_snapshot = tracker.to_dict()
        # Update with no bodies
        mcp_empty = _make_mcp_with_bodies([])
        tracker.update(mcp_empty)
        delta = tracker.get_delta(old_snapshot)
        assert len(delta["bodies_removed"]) == 1
        assert delta["bodies_removed"][0]["name"] == "OldBody"
        assert delta["bodies_added"] == []

    def test_get_delta_bodies_modified(self):
        """Set state, update with changed volume/faces, verify delta shows bodies_modified."""
        tracker = DesignStateTracker()
        bodies_v1 = [{"name": "Body1", "volume": 10.0, "face_count": 6}]
        mcp_v1 = _make_mcp_with_bodies(bodies_v1)
        tracker.update(mcp_v1)
        old_snapshot = tracker.to_dict()
        # Modify volume and face_count
        bodies_v2 = [{"name": "Body1", "volume": 15.0, "face_count": 10}]
        mcp_v2 = _make_mcp_with_bodies(bodies_v2)
        tracker.update(mcp_v2)
        delta = tracker.get_delta(old_snapshot)
        assert len(delta["bodies_modified"]) == 1
        mod = delta["bodies_modified"][0]
        assert mod["name"] == "Body1"
        assert mod["volume_before"] == 10.0
        assert mod["volume_after"] == 15.0
        assert mod["face_count_before"] == 6
        assert mod["face_count_after"] == 10

    def test_get_delta_no_changes(self):
        """Verify empty delta when state unchanged."""
        tracker = DesignStateTracker()
        bodies = [{"name": "Body1", "volume": 10.0, "face_count": 6}]
        mcp = _make_mcp_with_bodies(bodies)
        tracker.update(mcp)
        snapshot = tracker.to_dict()
        # No changes -- same state
        delta = tracker.get_delta(snapshot)
        assert delta["bodies_added"] == []
        assert delta["bodies_removed"] == []
        assert delta["bodies_modified"] == []

    def test_get_delta_timeline_change(self):
        """Verify timeline_position_change in delta."""
        tracker = DesignStateTracker()
        # First state: 2 timeline entries
        mcp1 = _make_mcp_with_bodies([], timeline=[{"i": 0}, {"i": 1}])
        tracker.update(mcp1)
        old_snapshot = tracker.to_dict()
        assert old_snapshot["timeline_position"] == 2
        # Second state: 5 timeline entries
        mcp2 = _make_mcp_with_bodies([], timeline=[{"i": j} for j in range(5)])
        tracker.update(mcp2)
        delta = tracker.get_delta(old_snapshot)
        assert delta["timeline_position_change"] == (2, 5)

    def test_thread_safety_lock_exists(self):
        """Verify Lock attribute exists."""
        tracker = DesignStateTracker()
        assert hasattr(tracker, "_lock")
        assert isinstance(tracker._lock, type(threading.Lock()))

    def test_concurrent_updates_do_not_corrupt(self):
        """Actual thread safety test -- concurrent update() calls."""
        bodies = [{"name": "Body1", "volume": 5.0, "face_count": 6}]
        mcp = _make_mcp_with_bodies(bodies, component_count=1, timeline=[{"i": 0}])
        tracker = DesignStateTracker()
        errors = []

        def worker():
            try:
                for _ in range(10):
                    tracker.update(mcp)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Errors during concurrent updates: {errors}"
        # Verify state is consistent
        state = tracker.to_dict()
        assert state is not None
        assert isinstance(state["bodies"], list)
        assert isinstance(state["timeline_position"], int)

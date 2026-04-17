"""
ai/design_state_tracker.py
Persistent design state tracker for Fusion 360 CAD sessions.

Maintains a structured JSON model of the current design state (bodies,
sketches, timeline position, component count) and provides utilities for
computing deltas between snapshots.  Used by ClaudeClient to inject
accurate state information into condensation summaries and verification
results.

Thread-safe -- all public methods acquire ``_lock`` before mutating state.
"""

import copy
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DesignStateTracker:
    """Tracks the live Fusion 360 design state across tool calls.

    Usage::

        tracker = DesignStateTracker()
        tracker.update(mcp_server)       # refresh from Fusion 360
        snapshot = tracker.to_dict()     # serialisable snapshot
        summary  = tracker.to_summary_string()  # compact text
        delta    = tracker.get_delta(old_snapshot)  # what changed

    Optionally integrates with :class:`~ai.git_design_manager.GitDesignManager`
    to automatically checkpoint design state changes in git.
    """

    def __init__(self, git_manager=None) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = self._empty_state()
        self._git_manager = git_manager

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        """Return the canonical empty state structure."""
        return {
            "bodies": [],
            "sketches": [],
            "timeline_position": 0,
            "component_count": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all tracked state back to the empty default."""
        with self._lock:
            self._state = self._empty_state()

    def update(self, mcp_server) -> None:
        """Refresh state by querying the MCP server.

        Calls ``get_body_list`` and optionally ``get_body_properties`` for
        each body.  All calls are wrapped in try/except so the tracker
        degrades gracefully when running in simulation mode or when the
        Fusion 360 add-in is unavailable.

        Parameters:
            mcp_server: The ``MCPServer`` instance (or compatible mock).
        """
        new_state = self._empty_state()

        # -- Bodies --
        try:
            body_list = mcp_server.execute_tool("get_body_list", {})
            if isinstance(body_list, dict) and body_list.get("success", True):
                raw_bodies = body_list.get("bodies", [])
                new_state["component_count"] = body_list.get("component_count", 0)

                for b in raw_bodies:
                    body_info: dict[str, Any] = {
                        "name": b.get("name", "Unknown"),
                        "volume": b.get("volume", 0),
                        "face_count": 0,
                        "bounding_box": None,
                    }

                    # Optionally query detailed properties per body
                    try:
                        props = mcp_server.execute_tool(
                            "get_body_properties",
                            {"body_name": b.get("name", "")},
                        )
                        if isinstance(props, dict) and props.get("success", True):
                            body_info["face_count"] = props.get("face_count", 0)
                            body_info["volume"] = props.get("volume", body_info["volume"])
                            body_info["bounding_box"] = props.get("bounding_box", None)
                    except Exception as e:
                        # TASK-025: Log instead of silently swallowing
                        logger.debug(
                            "DesignStateTracker: get_body_properties failed for '%s': %s",
                            b.get("name", "?"), e,
                        )

                    new_state["bodies"].append(body_info)
        except Exception as exc:
            logger.debug("DesignStateTracker.update: get_body_list failed: %s", exc)

        # -- Timeline position --
        try:
            timeline = mcp_server.execute_tool("get_timeline", {})
            if isinstance(timeline, dict) and timeline.get("success", True):
                tl = timeline.get("timeline", [])
                new_state["timeline_position"] = len(tl)
        except Exception as exc:
            # TASK-025: Log instead of silently swallowing
            logger.debug("DesignStateTracker.update: get_timeline failed: %s", exc)

        # -- Sketches (best-effort, uses get_sketch_list if available) --
        try:
            sketch_list = mcp_server.execute_tool("get_sketch_list", {})
            if isinstance(sketch_list, dict) and sketch_list.get("success", True):
                for s in sketch_list.get("sketches", []):
                    new_state["sketches"].append({
                        "name": s.get("name", "Unknown"),
                        "profile_count": s.get("profile_count", 0),
                    })
        except Exception:
            pass  # tool may not exist; graceful degradation

        with self._lock:
            self._state = new_state

        # Git integration: auto-checkpoint when state changes
        if self._git_manager is not None:
            try:
                summary = self.to_summary_string()
                self._git_manager.checkpoint(
                    f"Design state update: {summary}",
                    state_data=new_state,
                )
            except Exception as exc:
                logger.debug(
                    "DesignStateTracker: git checkpoint failed: %s", exc,
                )

    def to_dict(self) -> dict[str, Any]:
        """Return a deep copy of the current state as a plain dict."""
        with self._lock:
            return copy.deepcopy(self._state)

    def to_summary_string(self) -> str:
        """Return a compact one-line summary suitable for condensation injection.

        Example output::

            Design State: 3 bodies [Body1 (vol=12.5cm3, 8 faces), Body2
            (vol=3.2cm3, 6 faces), Base (vol=45.0cm3, 14 faces)], 1 sketch,
            timeline pos 7, 1 component
        """
        with self._lock:
            state = copy.deepcopy(self._state)

        parts: list[str] = []

        # Bodies
        body_count = len(state["bodies"])
        if body_count:
            body_descs: list[str] = []
            for b in state["bodies"]:
                vol = b.get("volume", 0)
                faces = b.get("face_count", 0)
                name = b.get("name", "?")
                body_descs.append(f"{name} (vol={vol}cm3, {faces} faces)")
            parts.append(f"{body_count} bodies [{', '.join(body_descs)}]")
        else:
            parts.append("0 bodies")

        # Sketches
        sketch_count = len(state["sketches"])
        if sketch_count:
            parts.append(f"{sketch_count} sketch{'es' if sketch_count != 1 else ''}")

        # Timeline
        parts.append(f"timeline pos {state['timeline_position']}")

        # Components
        parts.append(f"{state['component_count']} component{'s' if state['component_count'] != 1 else ''}")

        return "Design State: " + ", ".join(parts)

    def get_delta(self, previous_state_dict: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Compare the current state to a previous snapshot.

        Parameters:
            previous_state_dict: A dict returned by a prior ``to_dict()``
                call.  If ``None``, an empty state is assumed.

        Returns:
            A dict describing what changed::

                {
                    "bodies_added": [...],
                    "bodies_removed": [...],
                    "bodies_modified": [...],   # with volume/face deltas
                    "timeline_position_change": (old, new),
                    "component_count_change": (old, new),
                }
        """
        prev = previous_state_dict or self._empty_state()
        with self._lock:
            curr = copy.deepcopy(self._state)

        prev_bodies_by_name: dict[str, dict] = {
            b["name"]: b for b in prev.get("bodies", [])
        }
        curr_bodies_by_name: dict[str, dict] = {
            b["name"]: b for b in curr.get("bodies", [])
        }

        prev_names = set(prev_bodies_by_name.keys())
        curr_names = set(curr_bodies_by_name.keys())

        bodies_added: list[dict[str, Any]] = []
        bodies_removed: list[dict[str, Any]] = []
        bodies_modified: list[dict[str, Any]] = []

        for name in curr_names - prev_names:
            bodies_added.append(curr_bodies_by_name[name])

        for name in prev_names - curr_names:
            bodies_removed.append(prev_bodies_by_name[name])

        for name in prev_names & curr_names:
            old_b = prev_bodies_by_name[name]
            new_b = curr_bodies_by_name[name]
            changes: dict[str, Any] = {"name": name}
            changed = False

            old_vol = old_b.get("volume", 0)
            new_vol = new_b.get("volume", 0)
            if old_vol != new_vol:
                changes["volume_before"] = old_vol
                changes["volume_after"] = new_vol
                changed = True

            old_faces = old_b.get("face_count", 0)
            new_faces = new_b.get("face_count", 0)
            if old_faces != new_faces:
                changes["face_count_before"] = old_faces
                changes["face_count_after"] = new_faces
                changed = True

            if changed:
                bodies_modified.append(changes)

        delta: dict[str, Any] = {
            "bodies_added": bodies_added,
            "bodies_removed": bodies_removed,
            "bodies_modified": bodies_modified,
            "timeline_position_change": (
                prev.get("timeline_position", 0),
                curr.get("timeline_position", 0),
            ),
            "component_count_change": (
                prev.get("component_count", 0),
                curr.get("component_count", 0),
            ),
        }

        return delta

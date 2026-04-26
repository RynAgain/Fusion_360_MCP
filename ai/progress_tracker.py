"""
ai/progress_tracker.py
TASK-234: Track "meaningful progress" vs "thrashing" in iteration budget.

Categorises each tool call as productive, thrashing, neutral, or restart,
and computes a thrashing ratio.  When the ratio exceeds a configurable
threshold, a warning message is returned for injection into the conversation.

Thread-safe -- all public methods acquire ``_lock`` before mutating state.
"""

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool categorisation tables
# ---------------------------------------------------------------------------

PRODUCTIVE_TOOLS: set[str] = {
    "create_box", "create_cylinder", "create_sphere", "create_sketch",
    "add_sketch_line", "add_sketch_circle", "add_sketch_rectangle", "add_sketch_arc",
    "extrude", "revolve", "add_fillet", "add_chamfer", "mirror_body",
    "create_component", "apply_material", "set_parameter",
    "export_stl", "export_step", "export_f3d",
}

THRASHING_TOOLS: set[str] = {
    "undo", "redo", "delete_body", "delete_feature",
}

RESTART_TOOLS: set[str] = {
    "new_document",
}

# Default thresholds
DEFAULT_THRASHING_RATIO_THRESHOLD: float = 0.6
DEFAULT_MIN_CALLS_FOR_WARNING: int = 10


class ProgressTracker:
    """Tracks productive vs thrashing tool calls during an agent turn.

    Usage::

        tracker = ProgressTracker()
        warning = tracker.record("create_box", result={"success": True})
        # warning is None when ratio is healthy
        warning = tracker.record("undo", result={"success": True})
        # warning is a string when thrashing threshold exceeded

    Parameters:
        thrashing_ratio_threshold: Ratio above which a warning is emitted.
        min_calls_for_warning: Minimum total actionable calls before the
            ratio check is applied.
    """

    def __init__(
        self,
        thrashing_ratio_threshold: float = DEFAULT_THRASHING_RATIO_THRESHOLD,
        min_calls_for_warning: int = DEFAULT_MIN_CALLS_FOR_WARNING,
    ) -> None:
        self._lock = threading.Lock()
        self._thrashing_ratio_threshold = thrashing_ratio_threshold
        self._min_calls_for_warning = min_calls_for_warning

        self._productive_count: int = 0
        self._thrashing_count: int = 0
        self._neutral_count: int = 0
        self._restart_count: int = 0
        self._warning_emitted: bool = False

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    @staticmethod
    def classify(tool_name: str, result: Optional[dict[str, Any]] = None) -> str:
        """Classify a tool call into a category.

        Parameters:
            tool_name: The MCP tool name.
            result: The tool result dict (used to check success for
                ``execute_script``).

        Returns:
            One of ``"productive"``, ``"thrashing"``, ``"neutral"``,
            or ``"restart"``.
        """
        if tool_name in RESTART_TOOLS:
            return "restart"

        if tool_name in THRASHING_TOOLS:
            return "thrashing"

        if tool_name in PRODUCTIVE_TOOLS:
            return "productive"

        # execute_script is productive if success, thrashing if error
        if tool_name == "execute_script":
            if result is not None:
                success = result.get("success", True)
                if not success:
                    return "thrashing"
            return "productive"

        # Everything else (get_*, take_screenshot, validate_design, etc.)
        return "neutral"

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        tool_name: str,
        result: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """Record a tool call and return a warning string if threshold exceeded.

        Parameters:
            tool_name: The MCP tool name.
            result: The tool result dict.

        Returns:
            A warning string if the thrashing threshold has been exceeded
            for the first time, otherwise ``None``.
        """
        category = self.classify(tool_name, result)

        with self._lock:
            if category == "productive":
                self._productive_count += 1
            elif category == "thrashing":
                self._thrashing_count += 1
            elif category == "restart":
                self._restart_count += 1
                # Restarts also count as thrashing for ratio purposes
                self._thrashing_count += 1
            else:
                self._neutral_count += 1

            return self._check_threshold()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def productive_count(self) -> int:
        with self._lock:
            return self._productive_count

    @property
    def thrashing_count(self) -> int:
        with self._lock:
            return self._thrashing_count

    @property
    def neutral_count(self) -> int:
        with self._lock:
            return self._neutral_count

    @property
    def restart_count(self) -> int:
        with self._lock:
            return self._restart_count

    @property
    def total_calls(self) -> int:
        with self._lock:
            return (
                self._productive_count
                + self._thrashing_count
                + self._neutral_count
            )

    @property
    def thrashing_ratio(self) -> float:
        """Return the thrashing ratio (thrashing / (productive + thrashing)).

        Returns 0.0 if no productive or thrashing calls have been made.
        """
        with self._lock:
            return self._thrashing_ratio_unlocked()

    def to_dict(self) -> dict[str, Any]:
        """Return a snapshot of the current counters."""
        with self._lock:
            return {
                "productive_count": self._productive_count,
                "thrashing_count": self._thrashing_count,
                "neutral_count": self._neutral_count,
                "restart_count": self._restart_count,
                "total_calls": (
                    self._productive_count
                    + self._thrashing_count
                    + self._neutral_count
                ),
                "thrashing_ratio": self._thrashing_ratio_unlocked(),
            }

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset all counters to zero."""
        with self._lock:
            self._productive_count = 0
            self._thrashing_count = 0
            self._neutral_count = 0
            self._restart_count = 0
            self._warning_emitted = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _thrashing_ratio_unlocked(self) -> float:
        """Compute thrashing ratio without acquiring the lock."""
        denominator = self._productive_count + self._thrashing_count
        if denominator == 0:
            return 0.0
        return self._thrashing_count / denominator

    def _check_threshold(self) -> Optional[str]:
        """Check if the thrashing threshold has been exceeded.

        Must be called while holding ``_lock``.

        Returns:
            Warning string on first threshold breach, ``None`` otherwise.
        """
        if self._warning_emitted:
            return None

        total = (
            self._productive_count
            + self._thrashing_count
            + self._neutral_count
        )
        if total < self._min_calls_for_warning:
            return None

        ratio = self._thrashing_ratio_unlocked()
        if ratio <= self._thrashing_ratio_threshold:
            return None

        self._warning_emitted = True
        warning = (
            f"[THRASHING WARNING] Only {self._productive_count}/{total} tool "
            f"calls produced lasting geometry. {self._thrashing_count} calls "
            f"were undos/deletes/failures. Consider changing your approach."
        )
        logger.warning(
            "TASK-234: Thrashing threshold exceeded: ratio=%.2f, "
            "productive=%d, thrashing=%d, neutral=%d, restarts=%d",
            ratio,
            self._productive_count,
            self._thrashing_count,
            self._neutral_count,
            self._restart_count,
        )
        return warning

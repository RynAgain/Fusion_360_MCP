"""
ai/repetition_detector.py
Detect when the agent is stuck in a tool-calling loop.

If Claude calls the same tool with the same arguments multiple times in
succession, or keeps hammering the same tool with different arguments,
this detector flags the pattern so a warning can be injected into the
conversation and the agent can be nudged toward a different approach.
"""
import json
import hashlib
import logging

logger = logging.getLogger(__name__)

MAX_IDENTICAL_CALLS: int = 3   # After 3 consecutive identical calls -> flag
MAX_SIMILAR_CALLS: int = 5     # After 5 calls to the same tool in the window
WINDOW_SIZE: int = 10           # Rolling look-back window

# TASK-022: Force-stop threshold -- after this many identical consecutive
# calls, the detector returns force_stop=True to signal hard blocking.
# TASK-157: Raised from 3 to 20 -- only force-stop on truly runaway repetition.
FORCE_STOP_IDENTICAL: int = 20

# TASK-157: Per-tool threshold overrides (tool_name -> count_before_warning).
# Iterative tools like execute_script and take_screenshot need higher thresholds
# because calling them repeatedly with *different* arguments is normal workflow.
_TOOL_THRESHOLDS: dict[str, int] = {
    "execute_script": 12,   # Scripts are iterative by nature
    "take_screenshot": 8,   # Screenshots are cheap, often needed for verification
    "undo": 8,              # Undo chains are normal error recovery
}
# Default threshold for tools not in _TOOL_THRESHOLDS
_DEFAULT_THRESHOLD: int = 5

# ---------------------------------------------------------------------------
# TASK-217: Tool categories for category-aware recovery suggestions
# ---------------------------------------------------------------------------

WEB_TOOLS = {"web_search", "web_fetch", "fusion_docs_search"}

CAD_TOOLS = {
    "execute_script", "get_body_list", "get_component_info", "get_sketch_info",
    "extrude", "revolve", "add_fillet", "add_chamfer", "create_sketch",
    "create_box", "create_cylinder", "create_sphere", "mirror_body",
    "add_sketch_line", "add_sketch_circle", "add_sketch_rectangle",
    "add_sketch_arc", "get_body_properties", "get_document_info",
    "take_screenshot", "undo", "redo", "delete_body", "save_document",
}

FILE_TOOLS = {"read_document", "write_file", "apply_diff", "list_files"}

# Web tool repetition alternatives
_WEB_ALTERNATIVES: dict[str, str] = {
    "web_search": (
        "Consider asking the user for the information directly, "
        "or try a completely different search approach."
    ),
    "web_fetch": (
        "Consider asking the user for the information directly, "
        "or try a completely different URL or search approach."
    ),
    "fusion_docs_search": (
        "Consider asking the user for the information directly, "
        "or try a completely different search approach."
    ),
}
_DEFAULT_WEB_ALTERNATIVE = (
    "Consider asking the user for the information directly, "
    "or try a completely different search approach."
)

# Default CAD alternative for tools not in the alternatives_map
_DEFAULT_CAD_ALTERNATIVE = (
    "Verify current design state with `get_body_list` before retrying."
)


class RepetitionDetector:
    """Detects repetitive tool calling patterns."""

    def __init__(
        self,
        max_identical: int = MAX_IDENTICAL_CALLS,
        max_similar: int = MAX_SIMILAR_CALLS,
        force_stop_threshold: int = FORCE_STOP_IDENTICAL,
    ):
        self.max_identical = max_identical
        self.max_similar = max_similar
        self.force_stop_threshold = force_stop_threshold
        self._history: list[tuple[str, str]] = []  # (tool_name, args_hash)
        # TASK-022: Track consecutive identical call count for hard blocking
        self._consecutive_identical: int = 0
        self._last_tool_key: str | None = None  # "tool_name:args_hash"

    def _get_similar_threshold(self, tool_name: str) -> int:
        """Return the similar-call warning threshold for a given tool.

        TASK-157: Per-tool overrides allow iterative tools (execute_script,
        take_screenshot, undo) to have higher thresholds than the default.
        """
        return _TOOL_THRESHOLDS.get(tool_name, _DEFAULT_THRESHOLD)

    def record(self, tool_name: str, arguments: dict) -> dict:
        """Record a tool call and check for repetition.

        Returns a dict with the following shape::

            {
                "repeated": bool,       # True when repetition detected
                "type": str | None,     # "identical" or "similar" or None
                "count": int,           # How many times this pattern occurred
                "message": str | None,  # Human-readable warning
                "force_stop": bool,     # True when hard blocking is warranted
            }
        """
        args_hash = self._hash_args(arguments)
        self._history.append((tool_name, args_hash))

        # TASK-022: Track consecutive identical calls for force-stop
        current_key = f"{tool_name}:{args_hash}"
        if current_key == self._last_tool_key:
            self._consecutive_identical += 1
        else:
            self._consecutive_identical = 1
            self._last_tool_key = current_key

        # Keep only recent history
        if len(self._history) > WINDOW_SIZE:
            self._history = self._history[-WINDOW_SIZE:]

        # -- Check for identical calls (same tool + same args in a row) --
        identical_count = 0
        for name, ahash in reversed(self._history):
            if name == tool_name and ahash == args_hash:
                identical_count += 1
            else:
                break  # stop at first non-matching entry

        if identical_count >= self.max_identical:
            # TASK-022 / TASK-157: Escalate to force_stop only after high threshold
            should_force_stop = self._consecutive_identical >= self.force_stop_threshold
            return {
                "repeated": True,
                "type": "identical",
                "count": identical_count,
                "force_stop": should_force_stop,
                "message": (
                    f"Tool '{tool_name}' called {identical_count} times with "
                    f"IDENTICAL arguments (exact same parameters). This is "
                    f"likely a loop -- the operation keeps failing the same way. "
                    f"Try a different approach."
                ),
            }

        # -- Check for similar calls (same tool, potentially different args) --
        # TASK-157: Use per-tool threshold instead of hardcoded default
        similar_threshold = self._get_similar_threshold(tool_name)
        similar_count = sum(
            1 for name, _ in self._history[-WINDOW_SIZE:] if name == tool_name
        )
        if similar_count >= similar_threshold:
            return {
                "repeated": True,
                "type": "similar",
                "count": similar_count,
                "force_stop": False,
                "message": (
                    f"Tool '{tool_name}' called {similar_count} times in "
                    f"recent history with DIFFERENT arguments. This may be "
                    f"normal iterative work, but consider whether progress "
                    f"is being made."
                ),
            }

        return {"repeated": False, "type": None, "count": 0, "message": None, "force_stop": False}

    def get_alternatives(self, tool_name: str, tool_input: dict) -> str:
        """Return tool-specific alternative suggestions for a repeated tool call.

        These suggestions guide the agent toward a different approach when it
        is stuck repeating the same (or similar) tool call.

        TASK-217: Tool-category-aware suggestions.  Web tools get web-specific
        recovery advice instead of CAD-centric defaults.

        Parameters:
            tool_name:  The name of the tool being repeated.
            tool_input: The arguments passed to the tool.

        Returns:
            A human-readable suggestion string.
        """
        # TASK-217: Check tool category first for category-level suggestions
        if tool_name in WEB_TOOLS:
            return _WEB_ALTERNATIVES.get(
                tool_name,
                _DEFAULT_WEB_ALTERNATIVE,
            )

        alternatives_map: dict[str, str] = {
            "extrude": (
                "Try `execute_script` for complex geometry, or check sketch "
                "profiles with `get_sketch_info`."
            ),
            "revolve": (
                "Try `execute_script` for complex geometry, or check sketch "
                "profiles with `get_sketch_info`."
            ),
            "add_fillet": (
                "Try a different radius value, or verify edge selection with "
                "`get_body_properties`."
            ),
            "add_chamfer": (
                "Try a different radius value, or verify edge selection with "
                "`get_body_properties`."
            ),
            "execute_script": (
                "Break the script into smaller steps, or check current state "
                "with `get_body_list` first."
            ),
            "take_screenshot": (
                "Screenshot already taken. Analyze the previous screenshot "
                "before taking another."
            ),
        }

        suggestion = alternatives_map.get(
            tool_name,
            _DEFAULT_CAD_ALTERNATIVE,
        )
        return suggestion

    def reset(self) -> None:
        """Clear history (e.g. on new conversation)."""
        self._history.clear()
        self._consecutive_identical = 0
        self._last_tool_key = None

    def _hash_args(self, arguments: dict) -> str:
        """Create a stable hash of tool arguments for comparison."""
        normalised = json.dumps(arguments, sort_keys=True, default=str)
        return hashlib.sha256(normalised.encode()).hexdigest()

    def get_stats(self) -> dict:
        """Return repetition detection statistics."""
        tool_counts: dict[str, int] = {}
        for name, _ in self._history:
            tool_counts[name] = tool_counts.get(name, 0) + 1
        return {
            "history_length": len(self._history),
            "tool_counts": tool_counts,
        }

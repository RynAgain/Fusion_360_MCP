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


class RepetitionDetector:
    """Detects repetitive tool calling patterns."""

    def __init__(
        self,
        max_identical: int = MAX_IDENTICAL_CALLS,
        max_similar: int = MAX_SIMILAR_CALLS,
    ):
        self.max_identical = max_identical
        self.max_similar = max_similar
        self._history: list[tuple[str, str]] = []  # (tool_name, args_hash)

    def record(self, tool_name: str, arguments: dict) -> dict:
        """Record a tool call and check for repetition.

        Returns a dict with the following shape::

            {
                "repeated": bool,       # True when repetition detected
                "type": str | None,     # "identical" or "similar" or None
                "count": int,           # How many times this pattern occurred
                "message": str | None,  # Human-readable warning
            }
        """
        args_hash = self._hash_args(arguments)
        self._history.append((tool_name, args_hash))

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
            return {
                "repeated": True,
                "type": "identical",
                "count": identical_count,
                "message": (
                    f"Tool '{tool_name}' called {identical_count} times with "
                    f"identical arguments. The operation may be failing "
                    f"repeatedly. Try a different approach."
                ),
            }

        # -- Check for similar calls (same tool, potentially different args) --
        similar_count = sum(
            1 for name, _ in self._history[-WINDOW_SIZE:] if name == tool_name
        )
        if similar_count >= self.max_similar:
            return {
                "repeated": True,
                "type": "similar",
                "count": similar_count,
                "message": (
                    f"Tool '{tool_name}' called {similar_count} times in "
                    f"recent history. Consider a different approach or using "
                    f"execute_script for complex operations."
                ),
            }

        return {"repeated": False, "type": None, "count": 0, "message": None}

    def get_alternatives(self, tool_name: str, tool_input: dict) -> str:
        """Return tool-specific alternative suggestions for a repeated tool call.

        These suggestions guide the agent toward a different approach when it
        is stuck repeating the same (or similar) tool call.

        Parameters:
            tool_name:  The name of the tool being repeated.
            tool_input: The arguments passed to the tool.

        Returns:
            A human-readable suggestion string.
        """
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
            "Verify current design state with `get_body_list` before retrying.",
        )
        return suggestion

    def reset(self) -> None:
        """Clear history (e.g. on new conversation)."""
        self._history.clear()

    def _hash_args(self, arguments: dict) -> str:
        """Create a stable hash of tool arguments for comparison."""
        normalised = json.dumps(arguments, sort_keys=True, default=str)
        return hashlib.md5(normalised.encode()).hexdigest()

    def get_stats(self) -> dict:
        """Return repetition detection statistics."""
        tool_counts: dict[str, int] = {}
        for name, _ in self._history:
            tool_counts[name] = tool_counts.get(name, 0) + 1
        return {
            "history_length": len(self._history),
            "tool_counts": tool_counts,
        }

"""
ai/repetition_detector.py
Detect when the agent is stuck in a tool-calling loop.

If Claude calls the same tool with the same arguments multiple times in
succession, or keeps hammering the same tool with different arguments,
this detector flags the pattern so a warning can be injected into the
conversation and the agent can be nudged toward a different approach.

TASK-227: Also provides ScriptErrorTracker for detecting repeated
execute_script failures with identical error signatures (error_type +
error_message).  This catches cases where the scripts are textually
different but produce the exact same error.

TASK-230: Also provides RebuildLoopDetector for detecting when the LLM
calls ``new_document`` multiple times in a conversation as a "start
fresh" strategy, wasting tool calls by rebuilding from scratch only to
hit the same errors.
"""
import json
import hashlib
import logging
import re
from typing import Any

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
            "create_box": (
                "You have already created a box. Check existing bodies with "
                "`get_body_list` before creating another. If you are "
                "rebuilding from scratch, STOP -- fix the specific failing "
                "step instead of restarting."
            ),
            "create_cylinder": (
                "You have already created a cylinder. Check existing bodies "
                "with `get_body_list` before creating another. If you are "
                "rebuilding from scratch, STOP -- fix the specific failing "
                "step instead of restarting."
            ),
            "create_sphere": (
                "You have already created a sphere. Check existing bodies "
                "with `get_body_list` before creating another."
            ),
            "create_sketch": (
                "You have already created a sketch. Check existing sketches "
                "with `get_sketch_info` before creating another."
            ),
            "new_document": (
                "DO NOT restart the design from scratch. Fix the specific "
                "failing step. Rebuilding wastes iteration budget and the "
                "same errors will recur."
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


# ---------------------------------------------------------------------------
# TASK-227: Script error signature tracking
# ---------------------------------------------------------------------------

# Default thresholds for script error repetition escalation.
SCRIPT_ERROR_WARN_THRESHOLD: int = 2    # Start warning after N occurrences
SCRIPT_ERROR_BLOCK_THRESHOLD: int = 3   # Escalate to BLOCKED after N

# Known Fusion 360 API script error corrections.
# Keys are (error_type, error_message) tuples (or a regex pattern for the
# message).  Values are human-readable correction hints.
KNOWN_SCRIPT_ERROR_CORRECTIONS: dict[tuple[str, str], str] = {
    (
        "AttributeError",
        "'BRepBody' object has no attribute 'areaProperties'",
    ): (
        "BRepBody has no areaProperties() method. Use the get_body_properties "
        "tool or check diagnostic_data in the error response."
    ),
    (
        "AttributeError",
        "'BRepBody' object has no attribute 'volumeProperties'",
    ): (
        "BRepBody has no volumeProperties() method. Use the get_body_properties "
        "tool or check diagnostic_data."
    ),
    (
        "AttributeError",
        "'BRepBody' object has no attribute 'faceCount'",
    ): (
        "BRepBody uses body.faces.count, not body.faceCount."
    ),
    (
        "AttributeError",
        "module 'adsk.fusion' has no attribute 'ValueInput'",
    ): (
        "ValueInput is in adsk.core, not adsk.fusion. "
        "Use adsk.core.ValueInput or the pre-injected ValueInput."
    ),
    # TASK-240: XZ plane coordinate mapping errors.
    # On the XZ construction plane, sketch Y maps to world -Z.
    # Point3D.create(x, sketchY, 0) places at world (x, 0, -sketchY).
    # To place at world Z=7.0cm, use sketchY = -7.0.
    (
        "RuntimeError",
        "setDistanceExtent",
    ): (
        "setDistanceExtent failed. Common causes: (1) profile does not "
        "intersect the body -- verify sketch position with get_sketch_info, "
        "(2) XZ plane coordinate mapping: sketch Y = world -Z, so to place "
        "at world Z=7cm use sketchY=-7. (3) Use setOneSideExtent or "
        "ThroughAllExtentDefinition as alternatives."
    ),
    (
        "RuntimeError",
        "combineFeatures",
    ): (
        "combineFeatures failed. The correct API: input = "
        "combineFeatures.createInput(targetBody, toolBodies_ObjectCollection). "
        "Set input.operation = adsk.fusion.FeatureOperations.CutFeatureOperation. "
        "Then: combineFeatures.add(input). toolBodies must be an ObjectCollection."
    ),
    (
        "RuntimeError",
        "setCut",
    ): (
        "setCut(True) does not exist on CombineFeatureInput. Set "
        "input.operation = adsk.fusion.FeatureOperations.CutFeatureOperation "
        "instead."
    ),
}

# Compiled regex patterns for fuzzy matching known corrections.
# Built once at import time from KNOWN_SCRIPT_ERROR_CORRECTIONS.
_KNOWN_CORRECTION_PATTERNS: list[tuple[re.Pattern, str, str]] = []
for (_etype, _emsg), _hint in KNOWN_SCRIPT_ERROR_CORRECTIONS.items():
    try:
        _pat = re.compile(re.escape(_emsg), re.IGNORECASE)
        _KNOWN_CORRECTION_PATTERNS.append((_pat, _etype, _hint))
    except re.error:
        pass  # pragma: no cover


def _lookup_known_correction(error_type: str, error_message: str) -> str | None:
    """Return a correction hint if the error matches a known pattern.

    Performs exact (error_type, error_message) lookup first, then falls
    back to regex substring matching on the message.
    """
    # Exact match
    key = (error_type, error_message)
    if key in KNOWN_SCRIPT_ERROR_CORRECTIONS:
        return KNOWN_SCRIPT_ERROR_CORRECTIONS[key]

    # Fuzzy match via compiled patterns
    for pattern, known_etype, hint in _KNOWN_CORRECTION_PATTERNS:
        if error_type == known_etype and pattern.search(error_message):
            return hint

    return None


class ScriptErrorTracker:
    """Track repeated script error signatures across execute_script calls.

    TASK-227: The standard RepetitionDetector checks tool name + argument
    hash, so textually-different scripts that produce the *same* runtime
    error are never detected.  This tracker focuses on the **error
    signature** -- the (error_type, error_message) pair extracted from the
    tool result's ``error_details.script_error`` field.

    Usage::

        tracker = ScriptErrorTracker()
        info = tracker.record_error(tool_result)
        if info["repeated"]:
            # inject info["message"] into the conversation
            ...

    The tracker is meant to be used alongside RepetitionDetector, not as
    a replacement.
    """

    def __init__(
        self,
        warn_threshold: int = SCRIPT_ERROR_WARN_THRESHOLD,
        block_threshold: int = SCRIPT_ERROR_BLOCK_THRESHOLD,
    ):
        self.warn_threshold = warn_threshold
        self.block_threshold = block_threshold
        # Signature -> count
        self._counts: dict[tuple[str, str], int] = {}

    # ------------------------------------------------------------------
    # Signature extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_signature(tool_result: dict[str, Any]) -> tuple[str, str] | None:
        """Extract the (error_type, error_message) signature from a tool result.

        Looks in ``tool_result["error_details"]["script_error"]`` for the
        ``error_type`` and ``error_message`` fields produced by
        :func:`ai.error_classifier.parse_script_error`.

        Returns *None* when no script error signature can be extracted.
        """
        if not isinstance(tool_result, dict):
            return None

        error_details = tool_result.get("error_details")
        if not isinstance(error_details, dict):
            return None

        script_error = error_details.get("script_error")
        if not isinstance(script_error, dict):
            return None

        etype = script_error.get("error_type")
        emsg = script_error.get("error_message")
        if not etype or not emsg:
            return None

        return (str(etype).strip(), str(emsg).strip())

    # ------------------------------------------------------------------
    # Recording & escalation
    # ------------------------------------------------------------------

    def record_error(self, tool_result: dict[str, Any]) -> dict[str, Any]:
        """Record a script error and return escalation information.

        Parameters
        ----------
        tool_result : dict
            The result dict from an ``execute_script`` call that has
            already been enriched by :func:`ai.error_classifier.enrich_error`
            (i.e. it contains ``error_details.script_error``).

        Returns
        -------
        dict
            Always contains:

            - ``repeated`` (bool): Whether the signature has been seen
              before (count >= warn_threshold).
            - ``blocked`` (bool): Whether the error count has reached the
              block threshold.
            - ``count`` (int): Total occurrences of this signature.
            - ``signature`` (tuple | None): The (error_type, error_message)
              pair, or None if extraction failed.
            - ``message`` (str | None): A human-readable warning/block
              message, or None when not yet at threshold.
            - ``correction_hint`` (str | None): A known-correction hint
              from ``KNOWN_SCRIPT_ERROR_CORRECTIONS`` if available.
        """
        sig = self.extract_signature(tool_result)
        if sig is None:
            return {
                "repeated": False,
                "blocked": False,
                "count": 0,
                "signature": None,
                "message": None,
                "correction_hint": None,
            }

        error_type, error_message = sig
        self._counts[sig] = self._counts.get(sig, 0) + 1
        count = self._counts[sig]

        # Look up known correction
        correction = _lookup_known_correction(error_type, error_message)

        # Determine escalation level
        blocked = count >= self.block_threshold
        repeated = count >= self.warn_threshold

        message: str | None = None
        if blocked:
            message = (
                f"[BLOCKED] The error '{error_type}: {error_message}' "
                f"has occurred {count} times. Scripts producing this "
                f"error pattern will be rejected. Change your approach."
            )
            if correction:
                message += f" Hint: {correction}"
        elif repeated:
            message = (
                f"[SCRIPT ERROR REPEATED {count}x] The error "
                f"'{error_type}: {error_message}' has occurred "
                f"{count} times."
            )
            if correction:
                message += f" {correction}"
            else:
                message += (
                    " This API pattern does not work. Check "
                    "diagnostic_data in the error response for "
                    "alternative data, or use a different tool/approach."
                )

        if repeated:
            logger.warning(
                "TASK-227: Script error signature repeated %d times: %s: %s",
                count, error_type, error_message,
            )

        return {
            "repeated": repeated,
            "blocked": blocked,
            "count": count,
            "signature": sig,
            "message": message,
            "correction_hint": correction,
        }

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all tracked error signatures."""
        self._counts.clear()

    def get_counts(self) -> dict[tuple[str, str], int]:
        """Return a copy of the current error signature counts."""
        return dict(self._counts)

    def get_stats(self) -> dict[str, Any]:
        """Return summary statistics for diagnostics."""
        return {
            "tracked_signatures": len(self._counts),
            "total_errors": sum(self._counts.values()),
            "signatures": {
                f"{etype}:{emsg}": cnt
                for (etype, emsg), cnt in self._counts.items()
            },
        }


# ---------------------------------------------------------------------------
# TASK-230: Rebuild-from-scratch loop detection
# ---------------------------------------------------------------------------

# Default thresholds for rebuild loop escalation.
# TASK-240: Lowered from 2/3 -- even one rebuild should warn, two is critical.
# The convo_425 log shows 5+ full rebuilds consuming 10M+ tokens with zero progress.
REBUILD_WARN_THRESHOLD: int = 1    # Inject warning after first restart
REBUILD_CRITICAL_THRESHOLD: int = 2  # Escalate to CRITICAL after second restart


class RebuildLoopDetector:
    """Detect when the LLM restarts the design from scratch repeatedly.

    TASK-230: The LLM sometimes calls ``new_document`` multiple times in
    a conversation as a "start fresh" strategy, rebuilding 20+ features
    only to hit the same fundamental error each time.  This detector
    tracks those calls and returns escalating warnings.

    Usage::

        detector = RebuildLoopDetector()
        warning = detector.record_new_document(script_error_tracker)
        if warning:
            # inject warning into tool result or conversation
            result["rebuild_warning"] = warning

    The detector is intended to be used alongside :class:`ScriptErrorTracker`
    to include error summaries in the warnings.
    """

    def __init__(
        self,
        warn_threshold: int = REBUILD_WARN_THRESHOLD,
        critical_threshold: int = REBUILD_CRITICAL_THRESHOLD,
    ):
        self.warn_threshold = warn_threshold
        self.critical_threshold = critical_threshold
        self._count: int = 0

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_new_document(
        self,
        script_error_tracker: ScriptErrorTracker | None = None,
    ) -> str | None:
        """Record a ``new_document`` call and return a warning if appropriate.

        Parameters
        ----------
        script_error_tracker : ScriptErrorTracker, optional
            If provided, the warning will include unique error signatures
            from the tracker to help the LLM identify root causes.

        Returns
        -------
        str or None
            A warning string to inject into the tool result, or *None*
            if the threshold has not been reached yet.
        """
        self._count += 1

        if self._count < self.warn_threshold:
            return None

        # Collect unique error signatures from the script error tracker
        error_summary = self._get_error_summary(script_error_tracker)

        if self._count >= self.critical_threshold:
            msg = (
                f"[CRITICAL -- REBUILD LOOP] {self._count} design restarts "
                f"detected. You are wasting iteration budget by rebuilding "
                f"from scratch. The SAME errors will recur unless you change "
                f"your fundamental approach. DO NOT call new_document again. "
                f"Instead: (1) identify the root cause of the failure, "
                f"(2) explain it to the user, (3) ask for guidance."
            )
            if error_summary:
                msg += f" Previous unique errors: {error_summary}"
            msg += (
                " You MUST stop and explain the problem to the user rather "
                "than attempting another rebuild."
            )
            logger.warning(
                "TASK-230: Rebuild loop CRITICAL -- %d new_document calls",
                self._count,
            )
            return msg

        # warn_threshold <= count < critical_threshold
        msg = (
            f"[WARNING -- REBUILD DETECTED] You have restarted the design "
            f"{self._count} time(s). Rebuilding from scratch wastes tool "
            f"calls and rarely fixes the root cause. Previous errors: "
            f"{error_summary or 'unknown'}. Fix the specific failing step "
            f"rather than starting over. If the same error recurs, explain "
            f"the problem to the user."
        )
        logger.warning(
            "TASK-230: Rebuild loop WARNING -- %d new_document calls",
            self._count,
        )
        return msg

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_error_summary(
        tracker: ScriptErrorTracker | None,
    ) -> str:
        """Extract a compact list of unique error signatures from the tracker.

        Returns
        -------
        str
            Comma-separated list of ``"ErrorType: message"`` entries,
            or ``""`` if no errors are tracked.
        """
        if tracker is None:
            return ""
        stats = tracker.get_stats()
        sigs = stats.get("signatures", {})
        if not sigs:
            return ""
        # signatures dict keys are "ErrorType:message" strings
        # Limit to first 5 to keep the warning concise
        entries = list(sigs.keys())[:5]
        return ", ".join(entries)

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        """Return the current new_document call count."""
        return self._count

    def reset(self) -> None:
        """Clear the counter (e.g. on conversation clear)."""
        self._count = 0

    def get_stats(self) -> dict[str, Any]:
        """Return summary statistics for diagnostics."""
        return {
            "new_document_count": self._count,
            "warn_threshold": self.warn_threshold,
            "critical_threshold": self.critical_threshold,
        }

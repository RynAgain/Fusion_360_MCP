"""
ai/tool_recovery.py
TASK-225: Centralized tool-category-aware recovery strategies.
TASK-229: Diagnostic data summary extraction for LLM context injection.

Provides distinct failure/retry/fallback patterns for different tool
categories (web, CAD, file, document).  This module is the architectural
umbrella that ties together TASK-216 (error classifier tool-category
awareness), TASK-217 (repetition detector tool-category awareness),
and TASK-224 (web research budget).

Usage::

    from ai.tool_recovery import get_recovery_strategy

    strategy = get_recovery_strategy("web_search", "REFERENCE_ERROR", 4)
    if strategy["should_inject_system_message"]:
        messages.append({"role": "user", "content": strategy["system_message"]})

    from ai.tool_recovery import format_diagnostic_summary

    summary = format_diagnostic_summary(result.get("diagnostic_data", {}))
    if summary:
        result["diagnostic_summary"] = summary

The module can be consumed by ``ai/claude_client.py``,
``ai/error_classifier.py``, and ``ai/repetition_detector.py``.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool category definitions
#
# Imported from existing modules where possible for DRY consistency.
# If the import fails (e.g. circular import edge case), fall back to
# local definitions that mirror the authoritative sets.
# ---------------------------------------------------------------------------

try:
    from ai.error_classifier import WEB_TOOLS, CAD_TOOLS
except ImportError:  # pragma: no cover
    WEB_TOOLS = {"web_search", "web_fetch", "fusion_docs_search"}
    CAD_TOOLS = {
        "execute_script", "get_body_list", "get_component_info",
        "get_sketch_info", "extrude", "revolve", "add_fillet", "add_chamfer",
        "create_sketch", "create_box", "create_cylinder", "create_sphere",
        "mirror_body", "add_sketch_line", "add_sketch_circle",
        "add_sketch_rectangle", "add_sketch_arc", "get_body_properties",
        "get_document_info", "take_screenshot", "undo", "redo",
        "delete_body", "save_document",
    }

try:
    from ai.repetition_detector import FILE_TOOLS
except ImportError:  # pragma: no cover
    FILE_TOOLS = {"read_document", "write_file", "apply_diff", "list_files"}

# Document tools -- not defined elsewhere, so defined here as authoritative.
DOCUMENT_TOOLS: set[str] = {"read_document"}

# Re-export category sets for consumers that want a single import source.
__all__ = [
    "WEB_TOOLS",
    "CAD_TOOLS",
    "FILE_TOOLS",
    "DOCUMENT_TOOLS",
    "get_tool_category",
    "get_recovery_strategy",
    "format_diagnostic_summary",
]


# ---------------------------------------------------------------------------
# Category resolution
# ---------------------------------------------------------------------------

def get_tool_category(tool_name: str) -> str:
    """Return the category string for a given tool name.

    Returns one of ``"web"``, ``"cad"``, ``"document"``, ``"file"``,
    or ``"unknown"``.

    Note: ``DOCUMENT_TOOLS`` is checked before ``FILE_TOOLS`` because
    ``read_document`` appears in both sets (it is in ``FILE_TOOLS`` via
    the repetition_detector import).  Document is the more specific
    category.
    """
    if tool_name in WEB_TOOLS:
        return "web"
    if tool_name in CAD_TOOLS:
        return "cad"
    if tool_name in DOCUMENT_TOOLS:
        return "document"
    if tool_name in FILE_TOOLS:
        return "file"
    return "unknown"


# ---------------------------------------------------------------------------
# Per-category recovery strategies
# ---------------------------------------------------------------------------

# Default budget thresholds per category (consecutive failures before budget
# exhaustion).  Web tools have a strict budget because retries burn tokens
# with little chance of success.
_CATEGORY_BUDGETS: dict[str, int] = {
    "web": 3,
    "cad": 5,
    "file": 3,
    "document": 3,
}

# ---------------------------------------------------------------------------
# Web recovery
# ---------------------------------------------------------------------------

def _web_recovery(
    tool_name: str, error_type: str, consecutive_failures: int,
) -> dict[str, Any]:
    """Recovery strategy for web tools."""
    budget = _CATEGORY_BUDGETS["web"]
    exhausted = consecutive_failures >= budget

    if exhausted:
        return {
            "suggestion": (
                "Web research has failed repeatedly. Ask the user to "
                "provide the information directly, or proceed using "
                "your internal knowledge with appropriate caveats."
            ),
            "should_inject_system_message": True,
            "system_message": (
                f"[SYSTEM] Web research budget exhausted "
                f"({consecutive_failures} consecutive failures). "
                f"Ask the user to provide the information directly, "
                f"or proceed using your internal knowledge with "
                f"appropriate caveats."
            ),
            "should_block_retry": True,
        }

    # Not yet exhausted -- provide a suggestion but don't block.
    if error_type in ("REFERENCE_ERROR", "CONNECTION_ERROR"):
        suggestion = (
            "The URL was not found or unreachable. Try a different URL "
            "or search query."
        )
    elif error_type == "TIMEOUT_ERROR":
        suggestion = (
            "The web request timed out. Try a different URL or "
            "search query."
        )
    else:
        suggestion = (
            "The web request failed. Try rephrasing the search query "
            "or using a different URL."
        )

    return {
        "suggestion": suggestion,
        "should_inject_system_message": False,
        "system_message": "",
        "should_block_retry": False,
    }


# ---------------------------------------------------------------------------
# CAD recovery
# ---------------------------------------------------------------------------

def _cad_recovery(
    tool_name: str, error_type: str, consecutive_failures: int,
) -> dict[str, Any]:
    """Recovery strategy for CAD tools."""
    budget = _CATEGORY_BUDGETS["cad"]
    exhausted = consecutive_failures >= budget

    # Tool-specific suggestions
    _cad_suggestions: dict[str, str] = {
        "extrude": (
            "Check sketch profiles with get_sketch_info. Ensure the "
            "sketch has closed geometry and a valid profile."
        ),
        "revolve": (
            "Ensure the revolve axis does not intersect the profile. "
            "Use get_sketch_info to verify."
        ),
        "add_fillet": (
            "The fillet radius may be too large. Try a smaller value "
            "or verify edge selection with get_body_properties."
        ),
        "add_chamfer": (
            "The chamfer distance may be too large. Try a smaller "
            "value or verify edge selection with get_body_properties."
        ),
        "execute_script": (
            "Break the script into smaller steps. Check current "
            "design state with get_body_list before retrying."
        ),
    }

    default_suggestion = (
        "Verify current design state with get_body_list and "
        "get_timeline before retrying."
    )
    suggestion = _cad_suggestions.get(tool_name, default_suggestion)

    if exhausted:
        return {
            "suggestion": suggestion,
            "should_inject_system_message": True,
            "system_message": (
                f"[SYSTEM] CAD tool '{tool_name}' has failed "
                f"{consecutive_failures} consecutive times. Stop and "
                f"verify the design state with get_body_list and "
                f"get_timeline before attempting another approach."
            ),
            "should_block_retry": False,  # CAD tools may succeed with different params
        }

    return {
        "suggestion": suggestion,
        "should_inject_system_message": False,
        "system_message": "",
        "should_block_retry": False,
    }


# ---------------------------------------------------------------------------
# File recovery
# ---------------------------------------------------------------------------

def _file_recovery(
    tool_name: str, error_type: str, consecutive_failures: int,
) -> dict[str, Any]:
    """Recovery strategy for file tools."""
    budget = _CATEGORY_BUDGETS["file"]
    exhausted = consecutive_failures >= budget

    suggestion = (
        "Check the file path is correct and the file exists. "
        "Use list_files to verify available files."
    )

    if exhausted:
        return {
            "suggestion": suggestion,
            "should_inject_system_message": True,
            "system_message": (
                f"[SYSTEM] File operation '{tool_name}' has failed "
                f"{consecutive_failures} consecutive times. Verify "
                f"the file path and permissions before retrying."
            ),
            "should_block_retry": False,
        }

    return {
        "suggestion": suggestion,
        "should_inject_system_message": False,
        "system_message": "",
        "should_block_retry": False,
    }


# ---------------------------------------------------------------------------
# Document recovery
# ---------------------------------------------------------------------------

def _document_recovery(
    tool_name: str, error_type: str, consecutive_failures: int,
) -> dict[str, Any]:
    """Recovery strategy for document tools."""
    budget = _CATEGORY_BUDGETS["document"]
    exhausted = consecutive_failures >= budget

    suggestion = (
        "Check that the document path is correct and the file format "
        "is supported (PDF, DOCX, TXT, CSV)."
    )

    if exhausted:
        return {
            "suggestion": suggestion,
            "should_inject_system_message": True,
            "system_message": (
                f"[SYSTEM] Document operation '{tool_name}' has failed "
                f"{consecutive_failures} consecutive times. Ask the user "
                f"to provide the document content directly."
            ),
            "should_block_retry": False,
        }

    return {
        "suggestion": suggestion,
        "should_inject_system_message": False,
        "system_message": "",
        "should_block_retry": False,
    }


# ---------------------------------------------------------------------------
# Unknown / fallback recovery
# ---------------------------------------------------------------------------

def _unknown_recovery(
    tool_name: str, error_type: str, consecutive_failures: int,
) -> dict[str, Any]:
    """Fallback recovery for tools that do not belong to a known category."""
    return {
        "suggestion": (
            "Examine the error and try a different approach."
        ),
        "should_inject_system_message": False,
        "system_message": "",
        "should_block_retry": False,
    }


# ---------------------------------------------------------------------------
# TASK-229: Diagnostic data summary extraction
# ---------------------------------------------------------------------------

def _format_body_entry(body: dict) -> str:
    """Format a single body dict into a compact summary string.

    Parameters
    ----------
    body : dict
        A body dict typically containing ``name``, ``volume``,
        ``boundingBox`` (or ``bounding_box``).

    Returns
    -------
    str
        E.g. ``"Box (706.3cm3, 0,0,0 to 20,12,13)"``
    """
    name = body.get("name", "unnamed")

    # Volume -- may be absent or None
    volume = body.get("volume")
    vol_str = ""
    if volume is not None:
        try:
            vol_val = float(volume)
            # Use up to 1 decimal for readability
            vol_str = f"{vol_val:.1f}cm3"
        except (TypeError, ValueError):
            pass

    # Bounding box -- supports both camelCase and snake_case keys
    bbox = body.get("boundingBox") or body.get("bounding_box") or {}
    min_pt = bbox.get("min") or bbox.get("minPoint") or bbox.get("min_point") or {}
    max_pt = bbox.get("max") or bbox.get("maxPoint") or bbox.get("max_point") or {}

    bbox_str = ""
    if min_pt and max_pt:
        try:
            def _fmt_pt(pt: dict) -> str:
                x = round(pt.get("x", 0), 1)
                y = round(pt.get("y", 0), 1)
                z = round(pt.get("z", 0), 1)
                return f"{x},{y},{z}"
            bbox_str = f"{_fmt_pt(min_pt)} to {_fmt_pt(max_pt)}"
        except (TypeError, ValueError):
            pass

    # Compose entry
    parts = [name]
    detail_parts = []
    if vol_str:
        detail_parts.append(vol_str)
    if bbox_str:
        detail_parts.append(bbox_str)
    if detail_parts:
        parts.append(f"({', '.join(detail_parts)})")
    return " ".join(parts)


def format_diagnostic_summary(diagnostic_data: dict) -> str:
    """Extract a compact human-readable summary from diagnostic_data.

    TASK-229: When an ``execute_script`` error response includes
    ``diagnostic_data``, this function produces a short string like::

        "[DESIGN STATE] 3 bodies: Box (706.3cm3, 0,0,0 to 20,12,13), ..."

    This summary is injected as ``result["diagnostic_summary"]`` so the
    LLM has the data it needs without scripting.

    Parameters
    ----------
    diagnostic_data : dict
        The ``diagnostic_data`` dict from the tool result.  May contain
        ``body_list``, ``sketch_info``, ``body_properties``, etc.

    Returns
    -------
    str
        A compact summary string, or ``""`` if no useful data can be
        extracted.
    """
    if not isinstance(diagnostic_data, dict):
        return ""

    parts: list[str] = []

    # --- body_list summary ---
    body_list = diagnostic_data.get("body_list")
    if isinstance(body_list, dict):
        bodies = body_list.get("bodies", [])
        if isinstance(bodies, list) and bodies:
            count = len(bodies)
            body_summaries = [_format_body_entry(b) for b in bodies if isinstance(b, dict)]
            if body_summaries:
                parts.append(
                    f"{count} bodies: {', '.join(body_summaries)}"
                )
        elif body_list.get("count", 0) == 0:
            parts.append("0 bodies (empty design)")

    # --- sketch_info summary ---
    sketch_info = diagnostic_data.get("sketch_info")
    if isinstance(sketch_info, dict):
        sketch_name = sketch_info.get("name", "?")
        profile_count = sketch_info.get("profile_count")
        curve_count = sketch_info.get("curve_count")
        sketch_parts = [f"sketch '{sketch_name}'"]
        if profile_count is not None:
            sketch_parts.append(f"{profile_count} profiles")
        if curve_count is not None:
            sketch_parts.append(f"{curve_count} curves")
        parts.append(", ".join(sketch_parts))

    # --- body_properties summary ---
    body_props = diagnostic_data.get("body_properties")
    if isinstance(body_props, dict):
        prop_name = body_props.get("name", "?")
        prop_volume = body_props.get("volume")
        prop_area = body_props.get("area")
        prop_face_count = body_props.get("face_count")
        prop_parts = [f"body '{prop_name}'"]
        if prop_volume is not None:
            try:
                prop_parts.append(f"vol={float(prop_volume):.1f}cm3")
            except (TypeError, ValueError):
                pass
        if prop_area is not None:
            try:
                prop_parts.append(f"area={float(prop_area):.1f}cm2")
            except (TypeError, ValueError):
                pass
        if prop_face_count is not None:
            prop_parts.append(f"{prop_face_count} faces")
        parts.append(", ".join(prop_parts))

    if not parts:
        return ""

    return f"[DESIGN STATE] {'; '.join(parts)}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_CATEGORY_DISPATCHERS = {
    "web": _web_recovery,
    "cad": _cad_recovery,
    "file": _file_recovery,
    "document": _document_recovery,
}


def get_recovery_strategy(
    tool_name: str,
    error_type: str,
    consecutive_failures: int,
) -> dict[str, Any]:
    """Return a recovery strategy for a failed tool call.

    Parameters
    ----------
    tool_name : str
        The MCP tool that failed.
    error_type : str
        The classified error type (from ``ai.error_classifier``), e.g.
        ``"REFERENCE_ERROR"``, ``"GEOMETRY_ERROR"``, ``"TIMEOUT_ERROR"``.
    consecutive_failures : int
        Number of consecutive failures for this tool (or tool category).

    Returns
    -------
    dict
        A dict with the following keys:

        - ``suggestion`` (str): Human-readable recovery suggestion.
        - ``should_inject_system_message`` (bool): Whether to inject a
          system message into the conversation.
        - ``system_message`` (str): The message to inject (empty string
          when ``should_inject_system_message`` is False).
        - ``should_block_retry`` (bool): Whether further retries should
          be blocked (i.e. budget fully exhausted).
    """
    category = get_tool_category(tool_name)
    dispatcher = _CATEGORY_DISPATCHERS.get(category, _unknown_recovery)
    strategy = dispatcher(tool_name, error_type, consecutive_failures)

    logger.debug(
        "Recovery strategy for %s (category=%s, error=%s, failures=%d): %s",
        tool_name, category, error_type, consecutive_failures,
        strategy["suggestion"][:80],
    )

    return strategy


# ---------------------------------------------------------------------------
# TASK-237: Script error deduplication
# ---------------------------------------------------------------------------

def deduplicate_script_error(result: dict) -> dict:
    """Remove duplicate error information from a script execution result.

    TASK-237: When ``execute_script`` fails, the full traceback often appears
    in both ``stderr`` and ``error`` fields, plus a ``diagnostic_data`` block.
    This triples token cost per error.

    Deduplication rules:
    1. If ``stderr`` and ``error`` contain the same traceback text (or one
       is a substring of the other), remove ``error`` (keep ``stderr`` as
       the canonical source).
    2. If ``diagnostic_data`` is present AND ``diagnostic_summary`` has been
       generated, remove the raw ``diagnostic_data`` dict to save tokens
       (the summary is sufficient).
    3. Preserve all fields that contain unique information.

    Args:
        result: The tool result dict. Modified in-place and returned.

    Returns:
        The (possibly modified) result dict.
    """
    if not isinstance(result, dict):
        return result

    # Rule 1: Deduplicate stderr vs error
    stderr = result.get("stderr", "")
    error = result.get("error", "")

    if stderr and error and isinstance(stderr, str) and isinstance(error, str):
        stderr_stripped = stderr.strip()
        error_stripped = error.strip()
        if stderr_stripped and error_stripped:
            # Check if one contains the other (accounting for wrapper text)
            if (
                error_stripped in stderr_stripped
                or stderr_stripped in error_stripped
                or _traceback_overlap(stderr_stripped, error_stripped)
            ):
                del result["error"]
                logger.debug(
                    "TASK-237: Removed duplicate 'error' field "
                    "(same traceback as 'stderr')"
                )

    # Rule 2: Remove diagnostic_data when diagnostic_summary exists
    if "diagnostic_data" in result and "diagnostic_summary" in result:
        if result["diagnostic_summary"]:  # non-empty summary
            del result["diagnostic_data"]
            logger.debug(
                "TASK-237: Removed 'diagnostic_data' dict "
                "(diagnostic_summary is sufficient)"
            )

    return result


def _traceback_overlap(a: str, b: str) -> bool:
    """Check if two strings share a significant traceback block.

    Returns True if both contain a Python traceback and the traceback
    lines overlap substantially (> 50% of the shorter one's lines).
    """
    # Quick heuristic: both must contain "Traceback" to be traceback text
    if "Traceback" not in a or "Traceback" not in b:
        return False

    # Extract traceback lines from each
    a_lines = set(_extract_traceback_lines(a))
    b_lines = set(_extract_traceback_lines(b))

    if not a_lines or not b_lines:
        return False

    # Check overlap ratio against the smaller set
    overlap = a_lines & b_lines
    smaller = min(len(a_lines), len(b_lines))
    return len(overlap) / smaller > 0.5


def _extract_traceback_lines(text: str) -> list[str]:
    """Extract lines that look like Python traceback content."""
    lines = []
    in_traceback = False
    for line in text.split("\n"):
        stripped = line.strip()
        if "Traceback" in stripped:
            in_traceback = True
        if in_traceback and stripped:
            lines.append(stripped)
    return lines

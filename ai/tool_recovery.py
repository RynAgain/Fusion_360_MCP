"""
ai/tool_recovery.py
TASK-225: Centralized tool-category-aware recovery strategies.

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

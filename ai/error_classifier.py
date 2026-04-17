"""
ai/error_classifier.py
Error classification and recovery suggestions for Fusion 360 operations.

Classifies error messages by pattern matching, provides tool-specific recovery
suggestions, and determines whether auto-undo is appropriate for failed operations.
"""
import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error type constants
# ---------------------------------------------------------------------------

GEOMETRY_ERROR = "GEOMETRY_ERROR"
REFERENCE_ERROR = "REFERENCE_ERROR"
PARAMETER_ERROR = "PARAMETER_ERROR"
SCRIPT_ERROR = "SCRIPT_ERROR"
CONNECTION_ERROR = "CONNECTION_ERROR"
API_ERROR = "API_ERROR"
TIMEOUT_ERROR = "TIMEOUT_ERROR"
UNKNOWN_ERROR = "UNKNOWN_ERROR"

# ---------------------------------------------------------------------------
# Pattern-based classification rules
# ---------------------------------------------------------------------------

_PATTERNS = [
    # Geometry errors
    (re.compile(r'self.intersect|self-intersect', re.I), GEOMETRY_ERROR),
    (re.compile(r'feature.*fail|failed.*feature', re.I), GEOMETRY_ERROR),
    (re.compile(r'no.*profile|profile.*not.*found|empty.*profile', re.I), GEOMETRY_ERROR),
    (re.compile(r'invalid.*geometry|geometry.*invalid', re.I), GEOMETRY_ERROR),
    (re.compile(r'cannot.*create|creation.*failed', re.I), GEOMETRY_ERROR),
    (re.compile(r'zero.*thickness|thin.*body', re.I), GEOMETRY_ERROR),
    (re.compile(r'intersect.*itself', re.I), GEOMETRY_ERROR),

    # Reference errors
    (re.compile(r'not found|does not exist|cannot find', re.I), REFERENCE_ERROR),
    (re.compile(r'invalid.*reference|reference.*lost', re.I), REFERENCE_ERROR),
    (re.compile(r'no.*body.*named|no.*sketch.*named', re.I), REFERENCE_ERROR),
    (re.compile(r'index.*out.*range|out of range', re.I), REFERENCE_ERROR),

    # Parameter errors
    (re.compile(r'invalid.*value|value.*invalid', re.I), PARAMETER_ERROR),
    (re.compile(r'must be.*positive|negative.*not.*allowed', re.I), PARAMETER_ERROR),
    (re.compile(r'too.*small|too.*large|out.*of.*bounds', re.I), PARAMETER_ERROR),
    (re.compile(r'zero.*distance|zero.*radius', re.I), PARAMETER_ERROR),

    # Script errors
    (re.compile(r'SyntaxError|IndentationError|NameError|TypeError|AttributeError', re.I), SCRIPT_ERROR),
    (re.compile(r'traceback|line \d+', re.I), SCRIPT_ERROR),

    # Connection errors
    (re.compile(r'connection.*refused|connection.*reset|connection.*closed', re.I), CONNECTION_ERROR),
    (re.compile(r'socket.*error|tcp.*error|network', re.I), CONNECTION_ERROR),
    (re.compile(r'timeout|timed.*out', re.I), TIMEOUT_ERROR),
]

# ---------------------------------------------------------------------------
# Recovery suggestions per error type per tool
# ---------------------------------------------------------------------------

_SUGGESTIONS = {
    GEOMETRY_ERROR: {
        'default': 'Undo the failed operation and try with different geometry parameters.',
        'extrude': 'Check that the sketch has a closed profile. Use get_sketch_info to verify profile_count > 0.',
        'revolve': 'Ensure the revolve axis does not intersect the profile. Check sketch geometry.',
        'add_fillet': 'The fillet radius may be too large for the selected edges. Try a smaller radius.',
        'add_chamfer': 'The chamfer distance may be too large. Try a smaller distance.',
        'create_sketch': 'The target plane may be invalid. Try a standard construction plane (XY, XZ, YZ).',
    },
    REFERENCE_ERROR: {
        'default': 'The referenced entity was not found. Use get_body_list or get_component_info to find valid names.',
    },
    PARAMETER_ERROR: {
        'default': 'Check parameter values. All dimensions must be positive and in centimeters.',
    },
    SCRIPT_ERROR: {
        'default': 'Parse the traceback to identify the error line. Fix the Python code and re-execute.',
    },
    CONNECTION_ERROR: {
        'default': 'Connection to Fusion 360 was lost. Attempt to reconnect.',
    },
    TIMEOUT_ERROR: {
        'default': 'The operation timed out. Try simplifying the geometry or breaking the operation into smaller steps.',
    },
}

# ---------------------------------------------------------------------------
# Tools that support auto-undo recovery
# ---------------------------------------------------------------------------

AUTO_UNDO_TOOLS = {
    'extrude', 'revolve', 'add_fillet', 'add_chamfer',
    'mirror_body', 'create_cylinder', 'create_box', 'create_sphere',
    'add_sketch_line', 'add_sketch_circle', 'add_sketch_rectangle', 'add_sketch_arc',
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_error(error_message: str) -> str:
    """
    Classify an error message into an error type constant.

    Iterates through ``_PATTERNS`` in order and returns the type of the first
    matching pattern.  Returns ``UNKNOWN_ERROR`` when no pattern matches.
    """
    if not error_message:
        return UNKNOWN_ERROR

    for pattern, error_type in _PATTERNS:
        if pattern.search(error_message):
            return error_type

    return UNKNOWN_ERROR


def get_suggestion(error_type: str, tool_name: str = '') -> str:
    """
    Get a human-readable recovery suggestion for the given *error_type* and
    optional *tool_name*.

    Tool-specific suggestions take precedence over the default for that error
    type.
    """
    type_suggestions = _SUGGESTIONS.get(error_type, {})
    return type_suggestions.get(
        tool_name,
        type_suggestions.get('default', 'Examine the error and try a different approach.'),
    )


def should_auto_undo(error_type: str, tool_name: str) -> bool:
    """
    Determine whether an automatic undo should be performed after a failure.

    Auto-undo is only recommended for geometry-modifying tools that encounter
    ``GEOMETRY_ERROR`` or ``TIMEOUT_ERROR``.
    """
    if error_type in (GEOMETRY_ERROR, TIMEOUT_ERROR):
        return tool_name in AUTO_UNDO_TOOLS
    return False


def enrich_error(tool_name: str, error_message: str, result: dict = None) -> dict:
    """
    Enrich an error result dict with classification, suggestion, and recovery
    metadata.

    Parameters
    ----------
    tool_name : str
        The MCP tool that failed.
    error_message : str
        The raw error string (usually ``result['error']`` or ``result['message']``).
    result : dict, optional
        The original result dict returned by the bridge.  A minimal one is
        created if *None*.

    Returns
    -------
    dict
        A copy of *result* with ``error_type`` and ``error_details`` keys added.
    """
    error_type = classify_error(error_message)
    suggestion = get_suggestion(error_type, tool_name)
    auto_undo = should_auto_undo(error_type, tool_name)

    enriched = dict(result) if result else {'success': False, 'error': error_message}
    enriched['error_type'] = error_type
    enriched['error_details'] = {
        'suggestion': suggestion,
        'auto_undo_recommended': auto_undo,
        'tool_name': tool_name,
    }

    return enriched


# ---------------------------------------------------------------------------
# Prompt-based error policy (inspired by autoresearch crash classification)
# ---------------------------------------------------------------------------

class PromptErrorPolicy:
    """Encodes error handling policy as natural language directives
    for the AI agent, inspired by autoresearch's crash classification."""

    # Error categories with handling instructions
    CATEGORIES = {
        "transient": {
            "pattern_hints": ["timeout", "connection", "rate limit", "503", "429", "ECONNRESET"],
            "directive": "This is a transient error. Wait briefly and retry the same operation. Max 3 retries.",
            "severity": "low",
        },
        "trivial_bug": {
            "pattern_hints": ["typo", "missing parameter", "invalid argument", "TypeError", "KeyError", "AttributeError"],
            "directive": "This is likely a trivial bug. Fix the specific error and retry the operation.",
            "severity": "low",
        },
        "api_misuse": {
            "pattern_hints": ["not supported", "deprecated", "invalid API call", "permission denied", "Fusion 360 API error"],
            "directive": "This is an API misuse error. Review the Fusion 360 API documentation, correct the approach, and retry with a different method.",
            "severity": "medium",
        },
        "design_constraint": {
            "pattern_hints": ["constraint violation", "geometry error", "self-intersecting", "invalid body", "failed boolean"],
            "directive": "This is a design constraint violation. The current approach is fundamentally incompatible. Abandon this iteration, revert to the last good state, and try a completely different design approach.",
            "severity": "high",
        },
        "system_failure": {
            "pattern_hints": ["crash", "segfault", "out of memory", "fatal", "unrecoverable"],
            "directive": "This is a system failure. Do NOT retry. Log the error, save current state, and alert the user.",
            "severity": "critical",
        },
    }

    def classify_for_prompt(self, error_text: str) -> dict:
        """Classify an error string against pattern hints and return a directive.

        Matches *error_text* against each category's ``pattern_hints`` using
        case-insensitive substring matching.  Returns the first matching
        category.  If no category matches, returns ``"unknown"`` with a
        generic directive.

        Returns
        -------
        dict
            ``{"category": str, "directive": str, "severity": str}``
        """
        if not error_text:
            return {
                "category": "unknown",
                "directive": "Examine the error carefully and determine the best recovery strategy.",
                "severity": "unknown",
            }

        error_lower = error_text.lower()
        for category, info in self.CATEGORIES.items():
            for hint in info["pattern_hints"]:
                if hint.lower() in error_lower:
                    return {
                        "category": category,
                        "directive": info["directive"],
                        "severity": info["severity"],
                    }

        return {
            "category": "unknown",
            "directive": "Examine the error carefully and determine the best recovery strategy.",
            "severity": "unknown",
        }

    def get_error_policy_prompt(self) -> str:
        """Return a formatted error policy string for inclusion in system prompts.

        Lists all error categories with their pattern hints, severity levels,
        and handling directives in a clear reference-guide format.
        """
        lines = [
            "## Error Handling Policy",
            "",
            "When you encounter an error, classify it into one of the following "
            "categories and follow the corresponding directive:",
            "",
        ]

        for category, info in self.CATEGORIES.items():
            lines.append(f"### {category.replace('_', ' ').title()} (severity: {info['severity']})")
            lines.append(f"**Pattern hints:** {', '.join(info['pattern_hints'])}")
            lines.append(f"**Directive:** {info['directive']}")
            lines.append("")

        lines.append("### Unknown Error (severity: unknown)")
        lines.append("If the error does not match any of the above categories:")
        lines.append("**Directive:** Examine the error carefully and determine the best recovery strategy.")
        lines.append("")

        return "\n".join(lines)


def parse_script_error(stderr: str) -> dict:
    """
    Parse a Python traceback string (typically from ``execute_script``) and
    extract structured information.

    Returns
    -------
    dict
        Keys: ``line_number``, ``error_type``, ``error_message``,
        ``relevant_line``.
    """
    info = {
        'line_number': None,
        'error_type': None,
        'error_message': None,
        'relevant_line': None,
    }

    lines = stderr.strip().split('\n')

    # Find line number
    for line in lines:
        match = re.search(r'line (\d+)', line)
        if match:
            info['line_number'] = int(match.group(1))

    # Last line is usually the error
    if lines:
        last_line = lines[-1].strip()
        if ':' in last_line:
            parts = last_line.split(':', 1)
            info['error_type'] = parts[0].strip()
            info['error_message'] = parts[1].strip()
        else:
            info['error_message'] = last_line

    # Find the relevant source line (usually after a "File" line)
    for i, line in enumerate(lines):
        if line.strip().startswith('File') and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if not next_line.startswith('File') and not next_line.startswith('Traceback'):
                info['relevant_line'] = next_line

    return info

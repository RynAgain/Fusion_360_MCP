"""User custom instructions and mode-specific rules.

Expected context keys:
    user_additions (str): Extra instructions from the user's settings.
    mode_rules (str): Mode-specific rules text.
"""
import logging

logger = logging.getLogger(__name__)


def build(context: dict) -> str:
    """Build custom instructions section.

    Includes user additions from settings and mode-specific rules.

    Args:
        context: Runtime context dict.  Recognised keys:
            * ``user_additions`` -- free-form user instructions string.
            * ``mode_rules`` -- mode-specific rules text.
    """
    parts = []

    user_additions = context.get("user_additions", "")
    if user_additions and user_additions.strip():
        parts.append(f"## User Instructions\n{user_additions.strip()}")

    mode_rules = context.get("mode_rules", "")
    if mode_rules and mode_rules.strip():
        parts.append(f"## Mode-Specific Rules\n{mode_rules.strip()}")

    return "\n\n".join(parts)

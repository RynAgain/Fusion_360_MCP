"""Verification protocol for geometry operations.

Expected context keys:
    (none currently used -- reserved for future conditional verification)
"""


def build(context: dict) -> str:
    """Build the verification section of the system prompt.

    Args:
        context: Runtime context dict (currently unused, reserved for
            future conditional verification based on mode or provider).
    """
    return """\
## Verification Protocol
After geometry operations, verify your work immediately (do not pause):
1. Check tool result's delta (bodies_added, bodies_removed, volume changes)
2. If the result includes a warning, address it before proceeding
3. For visual confirmation, call take_screenshot
4. Compare actual dimensions against intended dimensions using get_body_properties
5. If verification fails, undo and retry with corrected parameters"""

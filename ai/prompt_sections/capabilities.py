"""Agent capabilities description.

Expected context keys:
    (none currently used -- reserved for future conditional capabilities)
"""


def build(context: dict) -> str:
    """Build the capabilities section of the system prompt.

    Args:
        context: Runtime context dict (currently unused, reserved for
            future conditional capabilities based on mode or provider).
    """
    return """\
## Capabilities
- Create and modify 3D geometry using sketch-profile-feature workflows
- Create primitives (boxes, cylinders, spheres) for quick prototyping
- Add features: fillets, chamfers, extrudes, revolves, mirrors, patterns
- Write and execute custom Python scripts inside Fusion 360 for complex operations
- Export designs in STL, STEP, and F3D formats
- Take screenshots to visually verify your work
- Manage design parameters, components, materials, and multiple documents"""

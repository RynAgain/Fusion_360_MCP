"""
ai/modes.py
CAD-specific mode system for Artifex360.

Each mode restricts the available tools and injects role-specific
instructions into the system prompt, keeping Claude focused on
the task at hand.
"""

import logging

from mcp.tool_groups import TOOL_GROUPS, get_tools_for_groups

logger = logging.getLogger(__name__)


class CadMode:
    """Definition of a CAD operating mode."""

    def __init__(
        self,
        slug: str,
        name: str,
        role_definition: str,
        tool_groups: list[str],
        custom_instructions: str = "",
    ):
        self.slug = slug
        self.name = name
        self.role_definition = role_definition
        self.tool_groups = tool_groups
        self.custom_instructions = custom_instructions

    def get_allowed_tools(self) -> set[str]:
        """Return the set of tool names available in this mode."""
        return get_tools_for_groups(self.tool_groups)

    def to_dict(self) -> dict:
        """Serialise to a JSON-friendly dict."""
        return {
            "slug": self.slug,
            "name": self.name,
            "role_definition": self.role_definition,
            "tool_groups": self.tool_groups,
            "custom_instructions": self.custom_instructions,
            "tool_count": len(self.get_allowed_tools()),
        }


# ---------------------------------------------------------------------------
# Predefined modes
# ---------------------------------------------------------------------------

DEFAULT_MODES: dict[str, CadMode] = {
    "full": CadMode(
        slug="full",
        name="Full Access",
        role_definition=(
            "You are a Fusion 360 AI Design Agent with full access to all "
            "tools. You can sketch, model, analyze, export, and write scripts."
        ),
        # TASK-039: Use dynamic call instead of static ALL_GROUPS capture
        tool_groups=list(TOOL_GROUPS.keys()),
        custom_instructions="",
    ),
    "sketch": CadMode(
        slug="sketch",
        name="Sketch Mode",
        role_definition=(
            "You are a 2D sketch specialist for Fusion 360. Focus on creating "
            "precise sketch geometry with proper constraints and dimensions. "
            "Always verify sketches have closed profiles before finishing."
        ),
        tool_groups=["document", "sketch", "query", "utility", "vision"],
        custom_instructions="""\
When working in Sketch Mode:
- Always use create_sketch first to create a sketch on the appropriate plane
- After adding geometry, use get_sketch_info to verify profile_count > 0
- Ensure all sketch profiles are closed before suggesting extrusion
- Use precise coordinates -- ask the user for dimensions if not specified
- Take screenshots to show the 2D sketch before moving to 3D""",
    ),
    "modeling": CadMode(
        slug="modeling",
        name="Modeling Mode",
        role_definition=(
            "You are a 3D modeling expert for Fusion 360. Focus on creating "
            "and modifying 3D geometry through sketches, features, and "
            "primitives. Always verify your work with screenshots and queries."
        ),
        tool_groups=[
            "document", "sketch", "primitives", "features",
            "body_ops", "query", "utility", "vision",
        ],
        custom_instructions="""\
When working in Modeling Mode:
- Follow the sketch-profile-feature workflow for precision
- Use primitives for quick prototyping
- Verify geometry after each major operation using get_body_properties
- Add fillets and chamfers last (they depend on edge topology)
- Save frequently""",
    ),
    "assembly": CadMode(
        slug="assembly",
        name="Assembly Mode",
        role_definition=(
            "You are a component and assembly specialist for Fusion 360. "
            "Focus on organizing designs into components, applying materials, "
            "and managing parameters for design intent."
        ),
        tool_groups=["document", "body_ops", "query", "utility", "vision"],
        custom_instructions="""\
When working in Assembly Mode:
- Create components before adding geometry to them
- Use meaningful component names
- Apply materials for visual clarity and mass calculations
- Use set_parameter for parametric dimensions
- Use get_component_info to understand the component tree""",
    ),
    "analysis": CadMode(
        slug="analysis",
        name="Analysis Mode",
        role_definition=(
            "You are a design analysis specialist for Fusion 360. Focus on "
            "inspecting geometry, measuring distances, validating designs, "
            "and reporting on body properties."
        ),
        tool_groups=["document", "query", "vision", "utility"],
        custom_instructions="""\
When working in Analysis Mode:
- Use get_body_properties for volume, surface area, and center of mass
- Use measure_distance for clearance checks
- Use validate_design before export to catch issues
- Use get_face_info to understand surface types and normals
- Take screenshots to document your analysis
- Present findings in a clear, structured format""",
    ),
    "export": CadMode(
        slug="export",
        name="Export Mode",
        role_definition=(
            "You are an export and manufacturing preparation specialist for "
            "Fusion 360. Focus on validating designs and exporting in the "
            "correct formats."
        ),
        tool_groups=["document", "query", "export", "vision", "utility"],
        custom_instructions="""\
When working in Export Mode:
- Always run validate_design before exporting
- Check for non-solid bodies that may cause issues
- Recommend the appropriate format: STL for 3D printing, STEP for manufacturing, F3D for archiving
- Verify export file sizes in the results
- Take a final screenshot before export for documentation""",
    ),
    "scripting": CadMode(
        slug="scripting",
        name="Scripting Mode",
        role_definition=(
            "You are a Fusion 360 scripting expert. You write Python scripts "
            "that use the adsk.core and adsk.fusion APIs to perform complex "
            "operations not covered by predefined tools."
        ),
        tool_groups=["document", "scripting", "query", "vision", "utility"],
        custom_instructions="""\
When working in Scripting Mode:
- Write complete, self-contained Python scripts
- Scripts have access to: adsk, app, design, rootComp, ui
- All measurements are in centimeters
- Capture results in a 'result' variable
- Handle errors with try/except
- Test scripts incrementally -- don't write 200 lines at once
- Take screenshots after script execution to verify results""",
    ),
    "orchestrator": CadMode(
        slug="orchestrator",
        name="Orchestrator",
        role_definition=(
            "You are the Artifex360 Orchestrator -- a strategic coordinator "
            "that decomposes complex CAD design requests into discrete, "
            "manageable subtasks and delegates each to the most appropriate "
            "specialist mode. You do NOT execute CAD operations directly. "
            "Instead, you plan the workflow, determine dependencies between "
            "steps, assign the optimal mode for each step, and synthesize "
            "results into a coherent design outcome. Your primary tools are "
            "querying the current design state and analyzing screenshots to "
            "verify progress."
        ),
        tool_groups=["query", "vision"],  # Read-only: can inspect but not modify
        custom_instructions="""\
ORCHESTRATION PROTOCOL:
1. DECOMPOSE: Break the user's request into atomic design steps
2. SEQUENCE: Determine dependencies between steps (what must complete before what)
3. ASSIGN: Select the optimal mode for each step based on its nature:
   - sketch: 2D geometry creation (lines, arcs, constraints, dimensions)
   - modeling: 3D feature operations (extrude, revolve, fillet, chamfer, shell)
   - assembly: Component positioning, joints, motion studies
   - analysis: Stress analysis, interference checks, mass properties
   - export: File format conversion, STL/STEP/IGES generation
   - scripting: Custom Fusion 360 API scripts for complex/repetitive operations
4. VERIFY: After each step completes, check the design state before proceeding
5. SYNTHESIZE: Combine all step results into a final summary for the user

RULES:
- Never execute CAD tools directly -- always delegate to subtasks
- Always verify design state between major steps
- If a step fails, assess whether to retry, skip, or redesign the approach
- Maintain awareness of the overall design goal throughout the workflow
- Provide clear progress updates to the user""",
    ),
}


class ModeManager:
    """Manages CAD operating modes."""

    def __init__(self):
        self._modes: dict[str, CadMode] = dict(DEFAULT_MODES)  # copy
        self._active_mode: str = "full"

    @property
    def active_mode(self) -> CadMode:
        """Return the currently active mode object."""
        return self._modes[self._active_mode]

    @property
    def active_slug(self) -> str:
        """Return the slug of the currently active mode."""
        return self._active_mode

    def switch_mode(self, slug: str) -> CadMode:
        """Switch to a different mode.

        Raises:
            ValueError: If the slug does not match any known mode.
        """
        if slug not in self._modes:
            raise ValueError(
                f"Unknown mode: {slug}. Available: {list(self._modes.keys())}"
            )
        self._active_mode = slug
        logger.info("Switched to mode: %s (%s)", slug, self._modes[slug].name)
        return self._modes[slug]

    def get_mode(self, slug: str) -> CadMode | None:
        """Return a mode by slug, or None if not found."""
        return self._modes.get(slug)

    def list_modes(self) -> list[dict]:
        """List all available modes as dicts."""
        return [m.to_dict() for m in self._modes.values()]

    def get_allowed_tools(self) -> set[str]:
        """Get tools allowed in the current mode."""
        return self.active_mode.get_allowed_tools()

    def add_custom_mode(self, slug: str, mode: CadMode) -> None:
        """Add a custom mode."""
        self._modes[slug] = mode

    def get_mode_prompt_additions(self) -> str:
        """Get mode-specific text to append to the system prompt."""
        mode = self.active_mode
        parts: list[str] = []
        if mode.slug != "full":
            parts.append(f"## Current Mode: {mode.name}")
            parts.append(mode.role_definition)
        if mode.custom_instructions:
            parts.append(mode.custom_instructions)
        return "\n\n".join(parts)

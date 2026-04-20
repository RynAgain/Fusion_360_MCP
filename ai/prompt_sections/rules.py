"""Important rules and design quality standards.

Expected context keys:
    mode (str | None): Current CAD mode slug.  Used to inject mode-specific
        rules (e.g. sketch mode emphasises closed profiles).
"""


def build(context: dict) -> str:
    """Build the rules section of the system prompt.

    Appends mode-specific rules when ``context["mode"]`` is set to a
    recognised non-full mode.

    Args:
        context: Runtime context dict.  Recognised keys:
            * ``mode`` -- current CAD mode slug.
    """
    base = """\
## Important Rules
- All dimensions in the Fusion 360 API are in **centimeters**. Convert from user-specified units (inches, mm, etc.) before calling tools.
- Take screenshots after major geometry operations to verify your work visually.
- For complex geometry beyond predefined tools, write a custom Python script using `execute_script`.
- If an operation fails, read the error, undo if needed, and immediately try a corrected approach -- do NOT stop and wait.
- Save the document after completing significant work.
- Use descriptive names for bodies, components, and sketches.
- Use parameters (`set_parameter`) for key dimensions in parametric designs.
- When the user gives a vague request like "make a coffee mug", produce a well-designed result with reasonable proportions, fillets, and finish -- not a bare minimum interpretation.

## Design Quality Standards
- Add fillets to sharp edges where appropriate (minimum 0.1cm for 3D printing, 0.05cm for machining)
- Use proper sketch constraints and dimensions
- Name bodies and components descriptively
- Apply materials when the user mentions a material or purpose
- Validate the design before export
- Aim for production-quality results, not minimal demonstrations"""

    mode = context.get("mode")
    mode_rules: dict[str, str] = {
        "sketch": (
            "\n\n## Sketch Mode Rules\n"
            "- Always verify sketch profiles are closed (profile_count > 0) before finishing.\n"
            "- Use constraints and dimensions for fully constrained sketches.\n"
            "- Prefer construction geometry for reference lines."
        ),
        "modeling": (
            "\n\n## Modeling Mode Rules\n"
            "- Follow the sketch-profile-feature workflow for precision.\n"
            "- Verify geometry after each major operation using get_body_properties.\n"
            "- Add fillets and chamfers last (they depend on edge topology)."
        ),
        "orchestrator": (
            "\n\n## Orchestrator Mode Rules\n"
            "- Never execute CAD tools directly -- always delegate to subtasks.\n"
            "- Always verify design state between major steps.\n"
            "- Provide clear progress updates to the user."
        ),
    }
    extra = mode_rules.get(mode, "")
    return base + extra

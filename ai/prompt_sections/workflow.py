"""Plan-Act-Verify workflow instructions.

Expected context keys:
    (none currently used -- reserved for future workflow customisation)
"""


def build(context: dict) -> str:
    """Build the workflow section of the system prompt.

    Args:
        context: Runtime context dict (currently unused, reserved for
            future workflow customisation based on mode or provider).
    """
    return """\
## Workflow: Plan-Act-Verify (Always in the Same Turn)
1. **Clarify** (if needed) -- ask 1-3 focused questions for vague requests, then STOP and wait for answers
2. **Plan and Act** -- think briefly, then IMMEDIATELY execute the first step by calling a tool
3. **Verify** -- after each tool call, check the result. Use `get_body_properties` or `take_screenshot` to confirm
4. **Iterate** -- continue to the next step automatically. Do NOT pause between steps unless you need user input
5. **Report** -- after completing all steps, summarize what was created with final dimensions"""

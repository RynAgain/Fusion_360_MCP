"""Context bridge for orchestrated subtask execution.

Assembles context packets that flow between the orchestrator and its
subtasks, ensuring each subtask has the information it needs without
exceeding token budgets.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Simple heuristic: ~4 characters per token (matches context_manager.py)
_CHARS_PER_TOKEN = 4


@dataclass
class SubtaskContext:
    """Context packet assembled for a subtask.

    Contains everything a subtask needs to execute its step,
    assembled by the ContextBridge.
    """

    step_index: int
    step_description: str
    mode: str  # Target mode slug

    # The overall plan context (what are we building?)
    plan_title: str
    plan_summary: str  # Markdown summary of the full plan with status

    # Results from dependency steps
    dependency_results: List[Dict[str, Any]] = field(default_factory=list)

    # Current design state
    design_state_summary: Optional[str] = None

    # Specific instructions for this subtask
    instructions: str = ""

    # Token budget info
    estimated_tokens: int = 0

    def to_system_context(self) -> str:
        """Render this context as a string to inject into the subtask's system prompt.

        Returns:
            A formatted markdown string containing the full subtask context.
        """
        sections: List[str] = []

        # Header
        sections.append("## Orchestrated Subtask")
        sections.append(f"**Plan:** {self.plan_title}")
        sections.append(
            f"**Current Step:** Step {self.step_index + 1}: {self.step_description}"
        )
        sections.append(f"**Target Mode:** {self.mode}")

        # Plan overview
        sections.append("")
        sections.append("### Plan Overview")
        sections.append(self.plan_summary)

        # Dependency results
        if self.dependency_results:
            sections.append("")
            sections.append("### Dependency Results")
            for dep in self.dependency_results:
                dep_index = dep.get("index", "?")
                dep_desc = dep.get("description", "")
                dep_result = dep.get("result", "")
                sections.append(f"**Step {dep_index + 1}: {dep_desc}**")
                sections.append(str(dep_result))

        # Design state
        if self.design_state_summary:
            sections.append("")
            sections.append("### Current Design State")
            sections.append(self.design_state_summary)

        # Instructions
        if self.instructions:
            sections.append("")
            sections.append("### Instructions")
            sections.append(self.instructions)

        # Important rules
        sections.append("")
        sections.append("### Important")
        sections.append(
            "- Focus ONLY on completing the current step described above"
        )
        sections.append(
            "- Do not attempt work belonging to other steps in the plan"
        )
        sections.append("- Verify your work before reporting completion")
        sections.append(
            "- Report a clear summary of what you accomplished when done"
        )

        return "\n".join(sections)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for logging/persistence."""
        return {
            "step_index": self.step_index,
            "step_description": self.step_description,
            "mode": self.mode,
            "plan_title": self.plan_title,
            "plan_summary": self.plan_summary,
            "dependency_results": list(self.dependency_results),
            "design_state_summary": self.design_state_summary,
            "instructions": self.instructions,
            "estimated_tokens": self.estimated_tokens,
        }


class ContextBridge:
    """Assembles context packets for orchestrated subtasks.

    Acts as the "memory" between the orchestrator and its subtasks,
    ensuring each subtask receives the right information to do its job.
    """

    def __init__(self, token_budget: int = 4000):
        """Initialize the context bridge.

        Args:
            token_budget: Maximum estimated tokens for context injection.
                          Defaults to 4000 tokens (~3000 words) to leave
                          room for the subtask's own system prompt and tools.
        """
        self._token_budget = token_budget
        # step_index -> {description, result, mode, completed_at}
        self._subtask_results: Dict[int, Dict[str, Any]] = {}

    def record_subtask_result(
        self, step_index: int, description: str, result: str, mode: str
    ) -> None:
        """Record the result of a completed subtask.

        Called by the orchestrator after a subtask completes so future
        subtasks can reference this result.

        Args:
            step_index: The step index that completed
            description: The step description
            result: The result summary from the subtask
            mode: The mode the subtask ran in
        """
        self._subtask_results[step_index] = {
            "description": description,
            "result": result,
            "mode": mode,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            "Recorded subtask result for step %d ('%s') in mode '%s'",
            step_index,
            description,
            mode,
        )

    def build_context(
        self,
        task_manager,
        design_state_tracker=None,
        step_index: Optional[int] = None,
        additional_instructions: str = "",
    ) -> SubtaskContext:
        """Build a SubtaskContext for a specific step.

        Args:
            task_manager: The TaskManager instance with the current plan
            design_state_tracker: Optional DesignStateTracker for current state
            step_index: The step to build context for. If None, uses
                       task_manager.auto_advance() to get the next step.
            additional_instructions: Extra instructions to include

        Returns:
            SubtaskContext ready to be injected into a subtask

        Raises:
            ValueError: If no step is available or step_index is invalid
        """
        # 1. Get the target step from task_manager
        if step_index is not None:
            tasks = task_manager.get_tasks()
            if not (0 <= step_index < len(tasks)):
                raise ValueError(
                    f"Invalid step_index {step_index}: plan has "
                    f"{len(tasks)} steps"
                )
            step = tasks[step_index]
        else:
            step = task_manager.auto_advance()
            if step is None:
                raise ValueError(
                    "No step available: plan may be complete, blocked, or empty"
                )

        # 2. Determine the mode (from step.mode_hint, falling back to "full")
        mode = step.mode_hint if step.mode_hint else "full"

        # 3. Gather dependency results
        dependency_results = self.get_dependency_results(step.index, task_manager)

        # 4. Get plan summary from task_manager
        plan_summary = task_manager.to_markdown()

        # 5. Get design state from design_state_tracker if provided
        design_state_summary = None
        if design_state_tracker is not None:
            try:
                design_state_summary = design_state_tracker.to_summary_string()
            except Exception as exc:
                logger.warning(
                    "Failed to get design state summary: %s", exc
                )

        # 6. Assemble the SubtaskContext
        context = SubtaskContext(
            step_index=step.index,
            step_description=step.description,
            mode=mode,
            plan_title=task_manager.get_plan_title() or "",
            plan_summary=plan_summary,
            dependency_results=dependency_results,
            design_state_summary=design_state_summary,
            instructions=additional_instructions,
        )

        # 7. Estimate tokens and truncate if over budget
        context.estimated_tokens = self._estimate_context_tokens(context)
        if context.estimated_tokens > self._token_budget:
            logger.info(
                "Context for step %d exceeds budget (%d > %d tokens), truncating",
                step.index,
                context.estimated_tokens,
                self._token_budget,
            )
            context = self._truncate_to_budget(context)

        # 8. Log the context assembly
        logger.info(
            "Built context for step %d ('%s') in mode '%s': "
            "%d dependency results, ~%d tokens",
            step.index,
            step.description,
            mode,
            len(dependency_results),
            context.estimated_tokens,
        )

        return context

    def get_dependency_results(
        self, step_index: int, task_manager
    ) -> List[Dict[str, Any]]:
        """Get results from all dependencies of a step.

        Only includes results for steps that are in self._subtask_results
        (i.e., completed subtasks). Steps that were completed outside the
        orchestrator (e.g., manually) won't have results here.

        Args:
            step_index: The step whose dependencies to look up
            task_manager: TaskManager to get the step's depends_on list

        Returns:
            List of dicts with {index, description, result, mode}
        """
        tasks = task_manager.get_tasks()
        if not (0 <= step_index < len(tasks)):
            return []

        step = tasks[step_index]
        results: List[Dict[str, Any]] = []

        for dep_index in step.depends_on:
            if dep_index in self._subtask_results:
                recorded = self._subtask_results[dep_index]
                results.append(
                    {
                        "index": dep_index,
                        "description": recorded["description"],
                        "result": recorded["result"],
                        "mode": recorded["mode"],
                    }
                )

        return results

    def clear(self) -> None:
        """Clear all recorded subtask results. Called when the plan is cleared."""
        self._subtask_results.clear()
        logger.info("Cleared all recorded subtask results")

    def get_results_summary(self) -> str:
        """Get a markdown summary of all recorded subtask results.

        Used by the orchestrator to track overall progress.
        """
        if not self._subtask_results:
            return "No subtask results recorded."

        lines: List[str] = ["## Subtask Results"]
        for index in sorted(self._subtask_results.keys()):
            entry = self._subtask_results[index]
            lines.append(
                f"- **Step {index + 1}** ({entry['mode']}): "
                f"{entry['description']}"
            )
            lines.append(f"  Result: {entry['result']}")

        return "\n".join(lines)

    @property
    def recorded_results(self) -> Dict[int, Dict[str, Any]]:
        """Read-only access to recorded subtask results."""
        return dict(self._subtask_results)

    def _estimate_context_tokens(self, context: SubtaskContext) -> int:
        """Estimate tokens for a SubtaskContext.

        Uses a simple heuristic: ~4 characters per token.
        """
        text = context.to_system_context()
        return len(text) // _CHARS_PER_TOKEN

    def _truncate_to_budget(self, context: SubtaskContext) -> SubtaskContext:
        """Truncate context if it exceeds the token budget.

        Truncation priority (what gets cut first):
        1. Dependency results (oldest first, keep most recent)
        2. Design state summary (truncate to last N lines)
        3. Plan summary (truncate to just the title)

        Never truncates: step_description, instructions, mode
        """
        # Work on a copy to avoid mutating before we know the result
        dep_results = list(context.dependency_results)
        design_state = context.design_state_summary
        plan_summary = context.plan_summary

        # Phase 1: Remove dependency results oldest first
        while dep_results and self._estimate_text_tokens(context, dep_results, design_state, plan_summary) > self._token_budget:
            removed = dep_results.pop(0)
            logger.debug(
                "Truncated dependency result for step %d to fit budget",
                removed.get("index", -1),
            )

        # Phase 2: Truncate design state summary to last N lines
        if design_state and self._estimate_text_tokens(context, dep_results, design_state, plan_summary) > self._token_budget:
            lines = design_state.split("\n")
            while len(lines) > 1 and self._estimate_text_tokens(context, dep_results, "\n".join(lines), plan_summary) > self._token_budget:
                lines.pop(0)
            design_state = "\n".join(lines) if lines else None

        # Phase 3: Truncate plan summary to just the title line
        if self._estimate_text_tokens(context, dep_results, design_state, plan_summary) > self._token_budget:
            # Keep only the first line (## Design Plan: ...)
            first_line = plan_summary.split("\n")[0] if plan_summary else ""
            plan_summary = first_line

        # Build the truncated context
        truncated = SubtaskContext(
            step_index=context.step_index,
            step_description=context.step_description,
            mode=context.mode,
            plan_title=context.plan_title,
            plan_summary=plan_summary,
            dependency_results=dep_results,
            design_state_summary=design_state,
            instructions=context.instructions,
        )
        truncated.estimated_tokens = self._estimate_context_tokens(truncated)
        return truncated

    def _estimate_text_tokens(
        self,
        context: SubtaskContext,
        dep_results: List[Dict[str, Any]],
        design_state: Optional[str],
        plan_summary: str,
    ) -> int:
        """Estimate tokens for a context with substituted fields.

        Helper for _truncate_to_budget to avoid rebuilding SubtaskContext
        objects on every iteration.
        """
        tmp = SubtaskContext(
            step_index=context.step_index,
            step_description=context.step_description,
            mode=context.mode,
            plan_title=context.plan_title,
            plan_summary=plan_summary,
            dependency_results=dep_results,
            design_state_summary=design_state,
            instructions=context.instructions,
        )
        return self._estimate_context_tokens(tmp)

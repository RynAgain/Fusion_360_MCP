"""
ai/context_window_guard.py
TASK-228: Context window size guard for complex tasks.

Estimates whether the configured context window is adequate for the current
task complexity and monitors runtime context pressure.  Emits warnings via
a pluggable callback when the context window is marginal or critically small.

Both :meth:`check_adequacy` and :meth:`check_pressure` accept an optional
``context_window`` parameter that represents the **actual** context window
of the model (e.g. from Ollama's ``/api/show`` endpoint).  When provided,
pressure and overhead calculations use ``context_window`` instead of the
user-facing ``max_tokens`` setting, which only controls the *output* token
budget.  This prevents false-positive critical warnings when a small
``max_tokens`` is paired with a large model context (e.g. ``max_tokens=8100``
on a 32k-context Qwen 3 model).
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adequacy levels
# ---------------------------------------------------------------------------

class AdequacyLevel(str, Enum):
    """Result of a context window adequacy check."""
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ContextWindowThresholds:
    """Configurable thresholds for context window adequacy checks.

    Attributes:
        critical_max_tokens:
            ``max_tokens`` below this value is always critical regardless
            of task complexity.
        warning_max_tokens:
            ``max_tokens`` below this value triggers a warning when the
            tool count is moderate (>= ``warning_tool_count``).
        warning_tool_count:
            Number of tools above which the warning threshold applies.
        tokens_per_tool:
            Estimated token cost of each tool definition (name +
            description + input_schema).
        min_free_tokens:
            Minimum free tokens (after system prompt + tool defs) below
            which a warning is emitted.
        critical_free_tokens:
            Free tokens below this value triggers a critical warning.
        pressure_warning_pct:
            Fraction of ``max_tokens`` at which a context-pressure
            warning is emitted (0.0-1.0).
        pressure_critical_pct:
            Fraction of ``max_tokens`` at which a critical context-
            pressure system message is injected (0.0-1.0).
    """
    critical_max_tokens: int = 8000
    warning_max_tokens: int = 16000
    warning_tool_count: int = 10
    tokens_per_tool: int = 350
    min_free_tokens: int = 4000
    critical_free_tokens: int = 2000
    pressure_warning_pct: float = 0.80
    pressure_critical_pct: float = 0.90


# ---------------------------------------------------------------------------
# Adequacy check result
# ---------------------------------------------------------------------------

@dataclass
class AdequacyResult:
    """Result of a context window adequacy check."""
    level: AdequacyLevel
    max_tokens: int
    estimated_overhead: int
    estimated_free: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "max_tokens": self.max_tokens,
            "estimated_overhead": self.estimated_overhead,
            "estimated_free": self.estimated_free,
            "reasons": list(self.reasons),
        }


# ---------------------------------------------------------------------------
# Context pressure result
# ---------------------------------------------------------------------------

@dataclass
class PressureResult:
    """Result of a runtime context pressure check."""
    usage_pct: float
    estimated_tokens_used: int
    max_tokens: int
    level: AdequacyLevel
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "usage_pct": round(self.usage_pct, 3),
            "estimated_tokens_used": self.estimated_tokens_used,
            "max_tokens": self.max_tokens,
            "level": self.level.value,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Context pressure system message
# ---------------------------------------------------------------------------

CONTEXT_PRESSURE_MESSAGE = (
    "[CONTEXT PRESSURE] You are running low on context window. "
    "Summarize your plan concisely and focus on the immediate next "
    "step only."
)

CRITICAL_CONCISENESS_MESSAGE = (
    "[SYSTEM] Your context window is very small for this task. "
    "Be extremely concise. Do NOT repeat information. Do NOT "
    "re-describe tools or parameters. Focus only on the immediate "
    "next action. If you lose track, ask the user to re-state the "
    "goal rather than guessing."
)


# ---------------------------------------------------------------------------
# ContextWindowGuard
# ---------------------------------------------------------------------------

class ContextWindowGuard:
    """Estimates context window adequacy and monitors runtime pressure.

    Usage::

        guard = ContextWindowGuard()
        result = guard.check_adequacy(
            max_tokens=8100,
            num_tools=35,
            system_prompt_tokens=800,
            message_count=0,
        )
        if result.level == AdequacyLevel.CRITICAL:
            # emit warning, inject conciseness message, etc.
            ...

        # During conversation:
        pressure = guard.check_pressure(
            max_tokens=8100,
            messages=conversation_history,
            system_prompt="...",
            num_tools=35,
        )
        if pressure.level != AdequacyLevel.OK:
            # emit context_pressure event
            ...
    """

    def __init__(
        self,
        thresholds: ContextWindowThresholds | None = None,
    ):
        self.thresholds = thresholds or ContextWindowThresholds()

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token estimate for English text.

        Uses the ``len(text) / 4`` heuristic which is a reasonable
        approximation for English content with typical tokenizers.
        """
        if not text:
            return 0
        return max(1, len(text) // 4)

    def estimate_messages_tokens(self, messages: list[dict]) -> int:
        """Estimate the total token count of a message list.

        Handles both string content and structured content blocks
        (text, tool_use, tool_result).
        """
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.estimate_tokens(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type", "")
                        if btype == "text":
                            total += self.estimate_tokens(block.get("text", ""))
                        elif btype == "tool_use":
                            # Tool name + serialized input
                            total += self.estimate_tokens(block.get("name", ""))
                            import json
                            total += self.estimate_tokens(
                                json.dumps(block.get("input", {}))
                            )
                        elif btype == "tool_result":
                            tc = block.get("content", "")
                            if isinstance(tc, str):
                                total += self.estimate_tokens(tc)
                            elif isinstance(tc, list):
                                for sub in tc:
                                    if isinstance(sub, dict):
                                        total += self.estimate_tokens(
                                            sub.get("text", "")
                                        )
                        elif btype == "image":
                            # Images consume significant tokens; estimate
                            # conservatively.
                            total += 1000
                    elif isinstance(block, str):
                        total += self.estimate_tokens(block)
            # Per-message overhead (role, separators, etc.)
            total += 4
        return total

    # ------------------------------------------------------------------
    # Adequacy check (startup / settings change)
    # ------------------------------------------------------------------

    def check_adequacy(
        self,
        max_tokens: int,
        num_tools: int = 0,
        system_prompt_tokens: int = 0,
        message_count: int = 0,
        context_window: int | None = None,
    ) -> AdequacyResult:
        """Check whether the context window is adequate for the given task.

        Args:
            max_tokens: The configured ``max_tokens`` value (output limit).
            num_tools: Number of tool definitions that will be sent.
            system_prompt_tokens: Estimated token count of the system prompt.
            message_count: Current number of messages in the conversation.
            context_window: The **actual** model context window size.  When
                provided, this is used for overhead/free-token calculations
                instead of ``max_tokens``.  For Ollama models, this comes
                from ``/api/show`` or the configured ``num_ctx``.

        Returns:
            An ``AdequacyResult`` with the adequacy level and reasons.
        """
        t = self.thresholds
        reasons: list[str] = []

        # Use context_window for capacity calculations when available;
        # fall back to max_tokens for backward compatibility.
        effective_capacity = context_window if context_window is not None else max_tokens

        # Estimate overhead: system prompt + tool definitions
        tool_tokens = num_tools * t.tokens_per_tool
        estimated_overhead = system_prompt_tokens + tool_tokens
        estimated_free = effective_capacity - estimated_overhead

        level = AdequacyLevel.OK

        # --- Absolute thresholds ---
        if effective_capacity < t.critical_max_tokens:
            level = AdequacyLevel.CRITICAL
            reasons.append(
                f"context window ({effective_capacity}) is below critical threshold "
                f"({t.critical_max_tokens})"
            )

        if effective_capacity < t.warning_max_tokens and num_tools >= t.warning_tool_count:
            new_level = AdequacyLevel.CRITICAL if level == AdequacyLevel.CRITICAL else AdequacyLevel.WARNING
            if new_level.value != AdequacyLevel.OK.value:
                if level == AdequacyLevel.OK:
                    level = new_level
                reasons.append(
                    f"context window ({effective_capacity}) is below warning threshold "
                    f"({t.warning_max_tokens}) with {num_tools} tools "
                    f"(>= {t.warning_tool_count})"
                )

        # --- Free-token thresholds ---
        if estimated_free < t.critical_free_tokens:
            if level != AdequacyLevel.CRITICAL:
                level = AdequacyLevel.CRITICAL
            reasons.append(
                f"Estimated free tokens ({estimated_free}) is below "
                f"critical minimum ({t.critical_free_tokens}). "
                f"Overhead: ~{estimated_overhead} tokens "
                f"(system prompt: {system_prompt_tokens}, "
                f"tools: {tool_tokens})"
            )
        elif estimated_free < t.min_free_tokens:
            if level == AdequacyLevel.OK:
                level = AdequacyLevel.WARNING
            reasons.append(
                f"Estimated free tokens ({estimated_free}) is below "
                f"recommended minimum ({t.min_free_tokens}). "
                f"Overhead: ~{estimated_overhead} tokens"
            )

        if not reasons:
            reasons.append("Context window appears adequate")

        return AdequacyResult(
            level=level,
            max_tokens=max_tokens,
            estimated_overhead=estimated_overhead,
            estimated_free=estimated_free,
            reasons=reasons,
        )

    # ------------------------------------------------------------------
    # Runtime context pressure monitoring
    # ------------------------------------------------------------------

    def check_pressure(
        self,
        max_tokens: int,
        messages: list[dict],
        system_prompt: str = "",
        num_tools: int = 0,
        context_window: int | None = None,
    ) -> PressureResult:
        """Estimate current context usage and return pressure level.

        Call this after each message exchange to detect when the
        conversation is approaching the context window limit.

        Args:
            max_tokens: The configured ``max_tokens`` value (output limit).
            messages: Current conversation history.
            system_prompt: The system prompt text.
            num_tools: Number of tool definitions.
            context_window: The **actual** model context window size.  When
                provided, pressure is calculated against this value instead
                of ``max_tokens``.

        Returns:
            A ``PressureResult`` with usage percentage and level.
        """
        t = self.thresholds

        # Use context_window for capacity calculations when available;
        # fall back to max_tokens for backward compatibility.
        effective_capacity = context_window if context_window is not None else max_tokens

        # Estimate total tokens in use
        prompt_tokens = self.estimate_tokens(system_prompt)
        tool_tokens = num_tools * t.tokens_per_tool
        message_tokens = self.estimate_messages_tokens(messages)
        total_used = prompt_tokens + tool_tokens + message_tokens

        if effective_capacity <= 0:
            return PressureResult(
                usage_pct=1.0,
                estimated_tokens_used=total_used,
                max_tokens=max_tokens,
                level=AdequacyLevel.CRITICAL,
                message="context window is zero or negative",
            )

        usage_pct = total_used / effective_capacity

        if usage_pct >= t.pressure_critical_pct:
            return PressureResult(
                usage_pct=usage_pct,
                estimated_tokens_used=total_used,
                max_tokens=max_tokens,
                level=AdequacyLevel.CRITICAL,
                message=CONTEXT_PRESSURE_MESSAGE,
            )
        elif usage_pct >= t.pressure_warning_pct:
            return PressureResult(
                usage_pct=usage_pct,
                estimated_tokens_used=total_used,
                max_tokens=max_tokens,
                level=AdequacyLevel.WARNING,
                message=(
                    f"Context usage at {usage_pct:.0%} of context window "
                    f"({total_used}/{effective_capacity} estimated tokens)"
                ),
            )
        else:
            return PressureResult(
                usage_pct=usage_pct,
                estimated_tokens_used=total_used,
                max_tokens=max_tokens,
                level=AdequacyLevel.OK,
            )

    # ------------------------------------------------------------------
    # Convenience: build system message for critical contexts
    # ------------------------------------------------------------------

    @staticmethod
    def get_conciseness_injection() -> str:
        """Return the system message to inject for critically small contexts."""
        return CRITICAL_CONCISENESS_MESSAGE

    @staticmethod
    def get_pressure_injection() -> str:
        """Return the system message to inject at critical context pressure."""
        return CONTEXT_PRESSURE_MESSAGE

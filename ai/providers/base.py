"""Base provider interface for LLM backends."""
import logging
import math
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# Minimum max_tokens floor for Anthropic contexts.
_ANTHROPIC_MIN_MAX_TOKENS = 8192


class LLMResponse:
    """Standardized response from any provider.

    Content blocks use the Anthropic-style format so the agent loop
    can process them uniformly:
        {"type": "text", "text": "..."}
        {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
    """

    def __init__(self):
        self.content: list[dict] = []
        self.stop_reason: str = ""  # "end_turn", "tool_use", "max_tokens"
        self.usage: dict = {"input_tokens": 0, "output_tokens": 0}
        self.model: str = ""


class BaseProvider(ABC):
    """Abstract base class for LLM providers."""

    @staticmethod
    def clamp_max_tokens(
        max_tokens: int,
        context_window: int,
        is_reasoning: bool = False,
    ) -> int:
        """Clamp *max_tokens* to prevent runaway token generation.

        For non-reasoning models the cap is ``min(max_tokens, ceil(context_window * 0.2))``.
        For reasoning models the provided *max_tokens* is used as-is, defaulting
        to 16,384 when not explicitly set.

        A floor of 8,192 is enforced for Anthropic-sized contexts (never
        returns less than ``_ANTHROPIC_MIN_MAX_TOKENS``).
        """
        if is_reasoning:
            clamped = max_tokens if max_tokens else 16_384
        else:
            cap = math.ceil(context_window * 0.2)
            clamped = min(max_tokens, cap)

        # Enforce floor
        clamped = max(clamped, _ANTHROPIC_MIN_MAX_TOKENS)

        if clamped < max_tokens:
            logger.info(
                "Clamped max_tokens from %d to %d (context_window=%d, reasoning=%s)",
                max_tokens, clamped, context_window, is_reasoning,
            )

        return clamped

    @abstractmethod
    def configure(self, **kwargs):
        """Configure the provider with connection details."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is properly configured and reachable."""

    @abstractmethod
    def create_message(
        self,
        messages: list,
        system: str,
        tools: list,
        max_tokens: int,
        model: str,
    ) -> LLMResponse:
        """Create a message (synchronous, non-streaming)."""

    @abstractmethod
    def stream_message(
        self,
        messages: list,
        system: str,
        tools: list,
        max_tokens: int,
        model: str,
        text_callback=None,
    ) -> LLMResponse:
        """Stream a message.

        Call *text_callback(chunk)* for each text delta.
        Returns the final complete response.
        """

    @abstractmethod
    def list_models(self) -> list[dict]:
        """List available models from this provider."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Provider type identifier (e.g. ``"anthropic"``, ``"ollama"``)."""

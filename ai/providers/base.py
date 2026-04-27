"""Base provider interface for LLM backends."""
import logging
import math
import time as _time
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
        # TASK-140: Initialize reasoning so callers can check without hasattr()
        self.reasoning: str | None = None


def _retry_on_transient(func, max_retries=3, base_delay=1.0):
    """Retry a function on transient API errors with exponential backoff."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as exc:
            exc_str = str(exc).lower()
            status = getattr(exc, 'status_code', getattr(exc, 'status', 0))
            # Retry on transient HTTP errors
            if status in (429, 500, 502, 503, 529) or \
               'overloaded' in exc_str or 'rate' in exc_str or \
               'timeout' in exc_str or 'connection' in exc_str:
                last_exc = exc
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Transient API error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, max_retries, delay, exc,
                )
                _time.sleep(delay)
                continue
            raise  # Non-transient, re-raise immediately
    raise last_exc


class BaseProvider(ABC):
    """Abstract base class for LLM providers."""

    @staticmethod
    def clamp_max_tokens(
        max_tokens: int,
        context_window: int,
        is_reasoning: bool = False,
        max_output: int = 0,
    ) -> int:
        """Clamp *max_tokens* to prevent runaway token generation.

        When *max_output* is provided (> 0), it is used as the ceiling
        instead of computing one from the context window.

        For non-reasoning models the cap is ``min(max_tokens, ceiling)``.
        For reasoning models the provided *max_tokens* is used as-is, defaulting
        to 16,384 when not explicitly set.

        A floor of ``_ANTHROPIC_MIN_MAX_TOKENS`` is enforced so we never
        return an unusably small value.
        """
        if is_reasoning:
            clamped = max_tokens if max_tokens else 16_384
        else:
            if max_output > 0:
                ceiling = max_output
            else:
                ceiling = int(context_window * 0.5)
            clamped = min(max_tokens, ceiling)

        # Enforce floor
        floor_val = max(_ANTHROPIC_MIN_MAX_TOKENS, min(1024, clamped))
        clamped = max(clamped, floor_val)

        # TASK-148: Log when clamping in EITHER direction (up or down)
        if clamped != max_tokens:
            logger.info(
                "Clamped max_tokens from %d to %d (floor=%d, ceiling=%s)",
                max_tokens, clamped, floor_val,
                ceiling if not is_reasoning else "N/A (reasoning)",
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
        reasoning_callback=None,
    ) -> LLMResponse:
        """Stream a message.

        Call *text_callback(chunk)* for each text delta.
        Call *reasoning_callback(chunk)* for each reasoning/thinking delta.
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

"""Base provider interface for LLM backends."""
from abc import ABC, abstractmethod


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

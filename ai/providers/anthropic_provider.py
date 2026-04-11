"""Anthropic Claude provider implementation."""
import logging

from ai.providers.base import BaseProvider, LLMResponse

logger = logging.getLogger(__name__)

ANTHROPIC_AVAILABLE = False
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    pass

ANTHROPIC_MODELS = [
    {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
    {"id": "claude-opus-4-20250514", "name": "Claude Opus 4"},
    {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet"},
]


class AnthropicProvider(BaseProvider):
    """LLM provider backed by the Anthropic Messages API."""

    def __init__(self):
        self._client = None
        self._api_key: str = ""

    # -- BaseProvider properties -------------------------------------------

    @property
    def name(self) -> str:
        return "Anthropic"

    @property
    def provider_type(self) -> str:
        return "anthropic"

    # -- Configuration -----------------------------------------------------

    def configure(self, api_key: str = "", **kwargs):
        self._api_key = api_key
        if ANTHROPIC_AVAILABLE and api_key:
            self._client = anthropic.Anthropic(api_key=api_key)
        else:
            self._client = None

    def is_available(self) -> bool:
        return ANTHROPIC_AVAILABLE and self._client is not None

    # -- Message creation --------------------------------------------------

    def create_message(self, messages, system, tools, max_tokens, model) -> LLMResponse:
        if not self.is_available():
            raise RuntimeError("Anthropic provider not configured")

        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        return self._convert_response(response)

    def stream_message(self, messages, system, tools, max_tokens, model,
                       text_callback=None) -> LLMResponse:
        if not self.is_available():
            raise RuntimeError("Anthropic provider not configured")

        try:
            with self._client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            ) as stream:
                if text_callback:
                    for text in stream.text_stream:
                        text_callback(text)
                response = stream.get_final_message()
            return self._convert_response(response)

        except (AttributeError, TypeError):
            # Older SDK without messages.stream -- fall back to sync call
            logger.info("Streaming unavailable; falling back to messages.create()")
            result = self.create_message(messages, system, tools, max_tokens, model)
            # Emit text blocks that were not streamed
            if text_callback:
                for block in result.content:
                    if block.get("type") == "text":
                        text_callback(block["text"])
            return result

    # -- Model listing -----------------------------------------------------

    def list_models(self) -> list[dict]:
        return ANTHROPIC_MODELS

    # -- Internal helpers --------------------------------------------------

    def _convert_response(self, response) -> LLMResponse:
        """Convert an Anthropic ``Message`` to a standardised ``LLMResponse``."""
        result = LLMResponse()
        result.model = response.model
        result.stop_reason = response.stop_reason

        if response.usage:
            result.usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

        result.content = []
        for block in response.content:
            if block.type == "text":
                result.content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                result.content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return result

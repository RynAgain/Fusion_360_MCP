"""
ai/summarization.py
Dedicated summarization provider for context condensation.

Allows using a different (potentially higher-quality) LLM provider for
conversation summarization than the main chat provider. This is useful
when running a small local model for chat but wanting Claude for summaries.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _extract_text_from_response(response) -> str | None:
    """Extract text from an LLM response, handling both string and
    list-of-blocks content formats.

    TASK-187: The Anthropic API returns response.content as a list of
    content blocks (e.g. [{"type": "text", "text": "..."}]), not a plain
    string.  This helper normalises both formats.

    Args:
        response: The LLM response object (or None).

    Returns:
        Extracted text string, or None if response is falsy.
    """
    if not response:
        return None

    content = getattr(response, "content", None)
    if content is None:
        return None

    if isinstance(content, list):
        return "".join(
            block.text
            if hasattr(block, "text")
            else (
                block.get("text", "")
                if isinstance(block, dict)
                else str(block)
            )
            for block in content
        )

    return str(content)


class SummarizationService:
    """Routes summarization requests to a dedicated or default provider.

    If summarization_provider is configured in settings, uses that provider.
    Otherwise, falls back to the main provider via the client's summarize() method.
    """

    def __init__(self):
        self._dedicated_provider = None
        self._dedicated_model = None

    def configure(self, provider_manager, settings) -> None:
        """Configure based on current settings.

        Args:
            provider_manager: The ProviderManager instance
            settings: The Settings instance
        """
        summ_provider = settings.get("summarization_provider")
        summ_model = settings.get("summarization_model")

        if summ_provider:
            provider = provider_manager.get_provider(summ_provider)
            if provider and provider.is_available():
                self._dedicated_provider = provider
                self._dedicated_model = summ_model
                logger.info(
                    "Summarization configured: provider=%s, model=%s",
                    summ_provider, summ_model or "default",
                )
            else:
                logger.warning(
                    "Summarization provider '%s' not available, falling back to main",
                    summ_provider,
                )
                self._dedicated_provider = None
        else:
            self._dedicated_provider = None
            self._dedicated_model = None

    @property
    def has_dedicated_provider(self) -> bool:
        """Whether a dedicated summarization provider is configured and available."""
        return self._dedicated_provider is not None

    def summarize(self, messages: list, max_tokens: int = 1024,
                  fallback_client=None) -> str | None:
        """Summarize messages using the dedicated or fallback provider.

        Args:
            messages: Messages to summarize
            max_tokens: Max output tokens
            fallback_client: ClaudeClient instance to use as fallback

        Returns:
            Summary text or None if summarization failed
        """
        if self._dedicated_provider:
            try:
                response = self._dedicated_provider.create_message(
                    messages=messages,
                    max_tokens=max_tokens,
                    model=self._dedicated_model,
                )
                return _extract_text_from_response(response)
            except Exception as exc:
                logger.warning("Dedicated summarization failed: %s", exc)
                # Fall through to fallback

        if fallback_client:
            return fallback_client.summarize(messages, max_tokens)

        return None

    def to_dict(self) -> dict:
        """Return current configuration for UI consumption."""
        return {
            "has_dedicated_provider": self.has_dedicated_provider,
            "provider": type(self._dedicated_provider).__name__ if self._dedicated_provider else None,
            "model": self._dedicated_model,
        }

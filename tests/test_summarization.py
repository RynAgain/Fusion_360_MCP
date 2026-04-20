"""
tests/test_summarization.py
Tests for ai/summarization.py -- dedicated summarization provider.
"""
import pytest
from unittest.mock import MagicMock, patch

from ai.summarization import SummarizationService, _extract_text_from_response


class TestSummarizationService:
    """Tests for the SummarizationService class."""

    def test_default_has_no_dedicated_provider(self):
        """A fresh SummarizationService has no dedicated provider."""
        svc = SummarizationService()
        assert svc.has_dedicated_provider is False

    def test_configure_with_valid_provider(self):
        """configure() sets the dedicated provider when available."""
        svc = SummarizationService()

        provider = MagicMock()
        provider.is_available.return_value = True

        pm = MagicMock()
        pm.get_provider.return_value = provider

        settings = MagicMock()
        settings.get.side_effect = lambda k: {
            "summarization_provider": "anthropic",
            "summarization_model": "claude-sonnet-4-20250514",
        }.get(k)

        svc.configure(pm, settings)

        assert svc.has_dedicated_provider is True
        pm.get_provider.assert_called_once_with("anthropic")

    def test_configure_with_unavailable_provider_falls_back(self):
        """configure() clears provider when the requested one is unavailable."""
        svc = SummarizationService()

        provider = MagicMock()
        provider.is_available.return_value = False

        pm = MagicMock()
        pm.get_provider.return_value = provider

        settings = MagicMock()
        settings.get.side_effect = lambda k: {
            "summarization_provider": "ollama",
            "summarization_model": None,
        }.get(k)

        svc.configure(pm, settings)

        assert svc.has_dedicated_provider is False

    def test_configure_with_none_provider(self):
        """configure() clears dedicated provider when setting is None."""
        svc = SummarizationService()
        # First set up a dedicated provider
        svc._dedicated_provider = MagicMock()
        assert svc.has_dedicated_provider is True

        pm = MagicMock()
        settings = MagicMock()
        settings.get.return_value = None

        svc.configure(pm, settings)

        assert svc.has_dedicated_provider is False

    def test_summarize_uses_dedicated_provider_with_string_content(self):
        """summarize() uses the dedicated provider -- string content format."""
        svc = SummarizationService()

        response = MagicMock()
        response.content = "This is a summary"

        provider = MagicMock()
        provider.create_message.return_value = response
        svc._dedicated_provider = provider
        svc._dedicated_model = "test-model"

        messages = [{"role": "user", "content": "Hello"}]
        result = svc.summarize(messages, max_tokens=512)

        assert result == "This is a summary"
        provider.create_message.assert_called_once_with(
            messages=messages, max_tokens=512, model="test-model",
        )

    def test_summarize_falls_back_to_client(self):
        """summarize() uses fallback_client when no dedicated provider."""
        svc = SummarizationService()

        fallback = MagicMock()
        fallback.summarize.return_value = "Fallback summary"

        messages = [{"role": "user", "content": "Hello"}]
        result = svc.summarize(messages, max_tokens=512, fallback_client=fallback)

        assert result == "Fallback summary"
        fallback.summarize.assert_called_once_with(messages, 512)

    def test_summarize_handles_exception_gracefully(self):
        """summarize() falls back on dedicated provider exception."""
        svc = SummarizationService()

        provider = MagicMock()
        provider.create_message.side_effect = RuntimeError("API error")
        svc._dedicated_provider = provider

        fallback = MagicMock()
        fallback.summarize.return_value = "Fallback after error"

        messages = [{"role": "user", "content": "Hello"}]
        result = svc.summarize(messages, fallback_client=fallback)

        assert result == "Fallback after error"

    def test_summarize_returns_none_when_all_fail(self):
        """summarize() returns None when no provider works and no fallback."""
        svc = SummarizationService()

        messages = [{"role": "user", "content": "Hello"}]
        result = svc.summarize(messages)

        assert result is None

    def test_to_dict_with_no_provider(self):
        """to_dict() returns correct data when no dedicated provider."""
        svc = SummarizationService()
        d = svc.to_dict()

        assert d["has_dedicated_provider"] is False
        assert d["provider"] is None
        assert d["model"] is None

    def test_to_dict_with_provider(self):
        """to_dict() returns provider class name and model."""
        svc = SummarizationService()
        svc._dedicated_provider = MagicMock()
        svc._dedicated_model = "claude-sonnet-4-20250514"

        d = svc.to_dict()

        assert d["has_dedicated_provider"] is True
        assert d["provider"] is not None
        assert d["model"] == "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# TASK-187: response.content block extraction
# ---------------------------------------------------------------------------

class TestExtractTextFromResponse:
    """TASK-187: Validate _extract_text_from_response helper."""

    def test_extracts_from_list_of_dict_blocks(self):
        """List of dict blocks with 'text' key should be concatenated."""
        response = MagicMock()
        response.content = [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "world"},
        ]
        result = _extract_text_from_response(response)
        assert result == "Hello world"

    def test_extracts_from_list_of_object_blocks(self):
        """List of objects with .text attribute should be concatenated."""
        block1 = MagicMock()
        block1.text = "Part one "
        block2 = MagicMock()
        block2.text = "part two"
        response = MagicMock()
        response.content = [block1, block2]
        result = _extract_text_from_response(response)
        assert result == "Part one part two"

    def test_handles_string_content(self):
        """Plain string content should be returned as-is."""
        response = MagicMock()
        response.content = "Just a string"
        result = _extract_text_from_response(response)
        assert result == "Just a string"

    def test_returns_none_for_none_response(self):
        """None response should return None."""
        result = _extract_text_from_response(None)
        assert result is None

    def test_returns_none_for_falsy_response(self):
        """Falsy response should return None."""
        result = _extract_text_from_response(0)
        assert result is None

    def test_handles_mixed_block_types(self):
        """Mixed dict and object blocks should all be handled."""
        obj_block = MagicMock()
        obj_block.text = "from object"
        dict_block = {"type": "text", "text": " from dict"}
        response = MagicMock()
        response.content = [obj_block, dict_block]
        result = _extract_text_from_response(response)
        assert result == "from object from dict"

    def test_handles_empty_list_content(self):
        """Empty list content should return empty string."""
        response = MagicMock()
        response.content = []
        result = _extract_text_from_response(response)
        assert result == ""

    def test_handles_none_content_attribute(self):
        """Response with content=None should return None."""
        response = MagicMock()
        response.content = None
        result = _extract_text_from_response(response)
        assert result is None


class TestSummarizationWithBlockContent:
    """TASK-187: Verify summarize() correctly extracts text from block responses."""

    def test_summarize_with_list_of_blocks_response(self):
        """summarize() should extract text from list-of-blocks content."""
        svc = SummarizationService()

        block = MagicMock()
        block.text = "This is a summary from blocks"
        response = MagicMock()
        response.content = [block]

        provider = MagicMock()
        provider.create_message.return_value = response
        svc._dedicated_provider = provider
        svc._dedicated_model = "test-model"

        messages = [{"role": "user", "content": "Hello"}]
        result = svc.summarize(messages, max_tokens=512)

        assert result == "This is a summary from blocks"

    def test_summarize_with_dict_blocks_response(self):
        """summarize() should extract text from dict-style content blocks."""
        svc = SummarizationService()

        response = MagicMock()
        response.content = [
            {"type": "text", "text": "Summary line 1. "},
            {"type": "text", "text": "Summary line 2."},
        ]

        provider = MagicMock()
        provider.create_message.return_value = response
        svc._dedicated_provider = provider
        svc._dedicated_model = "test-model"

        messages = [{"role": "user", "content": "Hello"}]
        result = svc.summarize(messages, max_tokens=512)

        assert result == "Summary line 1. Summary line 2."

    def test_summarize_with_none_response(self):
        """summarize() should return None when provider returns None."""
        svc = SummarizationService()

        provider = MagicMock()
        provider.create_message.return_value = None
        svc._dedicated_provider = provider
        svc._dedicated_model = "test-model"

        messages = [{"role": "user", "content": "Hello"}]
        result = svc.summarize(messages, max_tokens=512)

        assert result is None

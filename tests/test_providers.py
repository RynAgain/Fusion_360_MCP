"""
tests/test_providers.py
Unit tests for the LLM provider abstraction layer.

Tests cover:
  - LLMResponse default values
  - AnthropicProvider -- configure, is_available, list_models
  - OllamaProvider -- configure, default URL, message conversion, tool conversion
  - ProviderManager -- list, switch, configure, active provider
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from ai.providers.base import BaseProvider, LLMResponse
from ai.providers.anthropic_provider import AnthropicProvider, ANTHROPIC_MODELS
from ai.providers.ollama_provider import OllamaProvider, DEFAULT_OLLAMA_BASE_URL
from ai.providers.provider_manager import ProviderManager


# ---------------------------------------------------------------------------
# LLMResponse
# ---------------------------------------------------------------------------

class TestLLMResponse:
    """Verify LLMResponse defaults."""

    def test_default_content_is_empty(self):
        r = LLMResponse()
        assert r.content == []

    def test_default_stop_reason(self):
        r = LLMResponse()
        assert r.stop_reason == ""

    def test_default_usage(self):
        r = LLMResponse()
        assert r.usage == {"input_tokens": 0, "output_tokens": 0}

    def test_default_model(self):
        r = LLMResponse()
        assert r.model == ""

    def test_content_blocks_can_be_added(self):
        r = LLMResponse()
        r.content.append({"type": "text", "text": "hello"})
        r.content.append({"type": "tool_use", "id": "tc1", "name": "foo", "input": {}})
        assert len(r.content) == 2
        assert r.content[0]["type"] == "text"
        assert r.content[1]["type"] == "tool_use"


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------

class TestAnthropicProvider:
    """Verify AnthropicProvider without making real API calls."""

    def test_name(self):
        p = AnthropicProvider()
        assert p.name == "Anthropic"

    def test_provider_type(self):
        p = AnthropicProvider()
        assert p.provider_type == "anthropic"

    def test_not_available_without_configure(self):
        p = AnthropicProvider()
        # Without calling configure, no client is set
        assert p._client is None

    def test_configure_with_empty_key(self):
        p = AnthropicProvider()
        p.configure(api_key="")
        assert p._client is None

    def test_list_models_returns_known_models(self):
        p = AnthropicProvider()
        models = p.list_models()
        assert isinstance(models, list)
        assert len(models) > 0
        assert models == ANTHROPIC_MODELS
        # Each model should have id and name
        for m in models:
            assert "id" in m
            assert "name" in m

    @patch("ai.providers.anthropic_provider.ANTHROPIC_AVAILABLE", True)
    @patch("ai.providers.anthropic_provider.anthropic")
    def test_configure_with_valid_key_creates_client(self, mock_anthropic):
        """When the SDK is available and a key is provided, a client is created."""
        mock_anthropic.Anthropic.return_value = MagicMock()
        p = AnthropicProvider()
        p.configure(api_key="sk-ant-test-key")
        assert p._client is not None

    @patch("ai.providers.anthropic_provider.ANTHROPIC_AVAILABLE", True)
    @patch("ai.providers.anthropic_provider.anthropic")
    def test_is_available_with_client(self, mock_anthropic):
        mock_anthropic.Anthropic.return_value = MagicMock()
        p = AnthropicProvider()
        p.configure(api_key="sk-ant-test-key")
        assert p.is_available() is True

    @patch("ai.providers.anthropic_provider.ANTHROPIC_AVAILABLE", False)
    def test_not_available_without_sdk(self):
        p = AnthropicProvider()
        p.configure(api_key="sk-ant-test-key")
        assert p.is_available() is False


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------

class TestOllamaProvider:
    """Verify OllamaProvider configuration and message conversion."""

    def test_name(self):
        p = OllamaProvider()
        assert p.name == "Ollama"

    def test_provider_type(self):
        p = OllamaProvider()
        assert p.provider_type == "ollama"

    def test_default_base_url(self):
        p = OllamaProvider()
        assert p._base_url == DEFAULT_OLLAMA_BASE_URL

    def test_configure_custom_url(self):
        p = OllamaProvider()
        p.configure(base_url="http://myserver:11434/")
        assert p._base_url == "http://myserver:11434"  # trailing slash stripped

    def test_configure_empty_url_uses_default(self):
        p = OllamaProvider()
        p.configure(base_url="")
        assert p._base_url == DEFAULT_OLLAMA_BASE_URL

    @patch("ai.providers.ollama_provider.requests.get")
    def test_is_available_success(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        p = OllamaProvider()
        assert p.is_available() is True

    @patch("ai.providers.ollama_provider.requests.get")
    def test_is_available_failure(self, mock_get):
        mock_get.side_effect = ConnectionError("refused")
        p = OllamaProvider()
        assert p.is_available() is False

    @patch("ai.providers.ollama_provider.requests.get")
    def test_list_models_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"name": "llama3.1:latest", "size": 4000000000, "modified_at": "2024-01-01"},
                {"name": "qwen2.5:latest", "size": 3000000000, "modified_at": "2024-02-01"},
            ]
        }
        mock_get.return_value = mock_resp
        p = OllamaProvider()
        models = p.list_models()
        assert len(models) == 2
        assert models[0]["id"] == "llama3.1:latest"
        assert models[1]["name"] == "qwen2.5:latest"

    @patch("ai.providers.ollama_provider.requests.get")
    def test_list_models_failure(self, mock_get):
        mock_get.side_effect = ConnectionError("nope")
        p = OllamaProvider()
        models = p.list_models()
        assert models == []


class TestOllamaMessageConversion:
    """Test the Anthropic -> OpenAI message format conversion."""

    def setup_method(self):
        self.provider = OllamaProvider()

    def test_system_message_added(self):
        result = self.provider._convert_messages([], "You are a CAD expert")
        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are a CAD expert"

    def test_simple_string_messages(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = self.provider._convert_messages(messages, "")
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "hello"}
        assert result[1] == {"role": "assistant", "content": "hi there"}

    def test_user_text_blocks(self):
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "Make a box"},
            ]},
        ]
        result = self.provider._convert_messages(messages, "")
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Make a box"

    def test_assistant_tool_use_blocks(self):
        messages = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "I'll create that"},
                {"type": "tool_use", "id": "tc1", "name": "create_box",
                 "input": {"width": 10}},
            ]},
        ]
        result = self.provider._convert_messages(messages, "")
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "assistant"
        assert msg["content"] == "I'll create that"
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["function"]["name"] == "create_box"

    def test_tool_result_blocks(self):
        messages = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tc1",
                 "content": '{"success": true}'},
            ]},
        ]
        result = self.provider._convert_messages(messages, "")
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc1"

    def test_image_blocks_converted_to_text(self):
        messages = [
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "data": "abc123"}},
                {"type": "text", "text": "What's this?"},
            ]},
        ]
        result = self.provider._convert_messages(messages, "")
        # Image is converted to text description; real text follows
        user_msgs = [m for m in result if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert "[Image:" in user_msgs[0]["content"]


class TestOllamaToolConversion:
    """Test the Anthropic -> OpenAI tool definition conversion."""

    def setup_method(self):
        self.provider = OllamaProvider()

    def test_basic_tool_conversion(self):
        tools = [
            {
                "name": "create_box",
                "description": "Create a box primitive",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "number"},
                        "height": {"type": "number"},
                    },
                    "required": ["width", "height"],
                },
            },
        ]
        result = self.provider._convert_tools(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        func = result[0]["function"]
        assert func["name"] == "create_box"
        assert func["description"] == "Create a box primitive"
        assert "width" in func["parameters"]["properties"]

    def test_empty_tools(self):
        result = self.provider._convert_tools([])
        assert result == []


class TestOllamaResponseConversion:
    """Test the OpenAI -> LLMResponse conversion."""

    def setup_method(self):
        self.provider = OllamaProvider()

    def test_text_response(self):
        data = {
            "model": "llama3.1",
            "choices": [{
                "message": {"content": "Hello!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        result = self.provider._convert_response(data)
        assert isinstance(result, LLMResponse)
        assert result.model == "llama3.1"
        assert result.stop_reason == "end_turn"
        assert len(result.content) == 1
        assert result.content[0]["type"] == "text"
        assert result.content[0]["text"] == "Hello!"
        assert result.usage["input_tokens"] == 10
        assert result.usage["output_tokens"] == 5

    def test_tool_call_response(self):
        data = {
            "model": "llama3.1",
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_123",
                        "function": {
                            "name": "create_box",
                            "arguments": '{"width": 10}',
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 15},
        }
        result = self.provider._convert_response(data)
        assert result.stop_reason == "tool_use"
        assert len(result.content) == 1
        assert result.content[0]["type"] == "tool_use"
        assert result.content[0]["name"] == "create_box"
        assert result.content[0]["input"] == {"width": 10}

    def test_empty_choices(self):
        data = {"model": "llama3.1", "choices": [], "usage": {}}
        result = self.provider._convert_response(data)
        assert result.content == []
        assert result.stop_reason == ""

    @patch("ai.providers.ollama_provider.requests.post")
    def test_create_message(self, mock_post):
        """Test that create_message calls the correct endpoint."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "model": "llama3.1",
            "choices": [{
                "message": {"content": "Done!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        result = p.create_message(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=100,
            model="llama3.1",
        )
        assert result.content[0]["text"] == "Done!"
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/v1/chat/completions" in call_args[0][0]


# ---------------------------------------------------------------------------
# ProviderManager
# ---------------------------------------------------------------------------

class TestProviderManager:
    """Verify ProviderManager registry and switching."""

    def test_default_active_is_anthropic(self):
        pm = ProviderManager()
        assert pm.active_type == "anthropic"

    def test_active_returns_provider(self):
        pm = ProviderManager()
        assert isinstance(pm.active, BaseProvider)

    def test_list_providers(self):
        pm = ProviderManager()
        providers = pm.list_providers()
        assert len(providers) == 2
        types = [p["type"] for p in providers]
        assert "anthropic" in types
        assert "ollama" in types
        # Exactly one should be active
        active_count = sum(1 for p in providers if p["is_active"])
        assert active_count == 1

    def test_switch_to_ollama(self):
        pm = ProviderManager()
        provider = pm.switch("ollama")
        assert pm.active_type == "ollama"
        assert provider.provider_type == "ollama"

    def test_switch_to_anthropic(self):
        pm = ProviderManager()
        pm.switch("ollama")
        pm.switch("anthropic")
        assert pm.active_type == "anthropic"

    def test_switch_unknown_raises(self):
        pm = ProviderManager()
        with pytest.raises(ValueError, match="Unknown provider"):
            pm.switch("openai")

    def test_configure_provider(self):
        pm = ProviderManager()
        pm.configure_provider("ollama", base_url="http://custom:9999")
        ollama = pm.get_provider("ollama")
        assert ollama._base_url == "http://custom:9999"

    def test_get_provider_unknown_returns_none(self):
        pm = ProviderManager()
        assert pm.get_provider("openai") is None

    def test_list_models_delegates_to_active(self):
        pm = ProviderManager()
        models = pm.list_models()
        # Default active is anthropic -> returns ANTHROPIC_MODELS
        assert models == ANTHROPIC_MODELS

    def test_list_models_for_specific_provider(self):
        pm = ProviderManager()
        # Ollama list_models will likely fail (no server), returns []
        # but we can mock it
        with patch.object(pm.get_provider("ollama"), "list_models", return_value=[{"id": "m1", "name": "m1"}]):
            models = pm.list_models("ollama")
            assert len(models) == 1
            assert models[0]["id"] == "m1"

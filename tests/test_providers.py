"""
tests/test_providers.py
Unit tests for the LLM provider abstraction layer.

Tests cover:
  - LLMResponse default values
  - AnthropicProvider -- configure, is_available, list_models, prompt caching
  - AnthropicProvider -- reasoning budget, extended thinking, max-token clamping
  - OllamaProvider -- configure, default URL, message conversion, tool conversion
  - ProviderManager -- list, switch, configure, active provider
"""

import copy
import json
import math
import os
import time
import pytest
from unittest.mock import MagicMock, patch

from ai.providers.base import BaseProvider, LLMResponse, _ANTHROPIC_MIN_MAX_TOKENS
from ai.providers.anthropic_provider import (
    AnthropicProvider,
    ANTHROPIC_MODELS,
    DEFAULT_MODEL,
    _OUTPUT_128K_BETA,
    _CONTEXT_1M_BETA,
    get_model_info,
    get_effective_context_window,
)
from ai.providers.ollama_provider import (
    OllamaProvider,
    DEFAULT_OLLAMA_BASE_URL,
    OLLAMA_DEFAULT_MODEL_ID,
    OLLAMA_DEFAULT_MODEL_INFO,
    OLLAMA_SDK_AVAILABLE,
    _DEEPSEEK_R1_TEMPERATURE,
    _MODEL_CACHE_TTL,
    _DISK_CACHE_FILE,
)
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

    def test_default_reasoning_is_none(self):
        """TASK-140: reasoning should be initialized to None."""
        r = LLMResponse()
        assert r.reasoning is None

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

class TestAnthropicModelRegistry:
    """Verify the expanded model registry and helper functions."""

    def test_registry_is_dict(self):
        assert isinstance(ANTHROPIC_MODELS, dict)

    def test_registry_has_at_least_13_models(self):
        assert len(ANTHROPIC_MODELS) >= 13

    def test_default_model_exists_in_registry(self):
        assert DEFAULT_MODEL in ANTHROPIC_MODELS

    def test_get_model_info_known(self):
        info = get_model_info("claude-sonnet-4-6")
        assert info is not None
        assert info["max_tokens"] == 64000
        assert info["supports_prompt_cache"] is True

    def test_get_model_info_unknown(self):
        assert get_model_info("nonexistent-model") is None

    def test_all_models_have_required_fields(self):
        required = {"max_tokens", "context_window", "supports_images",
                     "supports_prompt_cache", "input_price", "output_price",
                     "cache_write_price", "cache_read_price", "description"}
        for model_id, meta in ANTHROPIC_MODELS.items():
            missing = required - set(meta.keys())
            assert not missing, f"{model_id} missing fields: {missing}"

    def test_thinking_model_has_reasoning_required(self):
        info = get_model_info("claude-3-7-sonnet-20250219:thinking")
        assert info is not None
        assert info.get("reasoning_required") is True

    def test_legacy_haiku_no_image_support(self):
        """claude-3-5-haiku-20241022 does not support images."""
        info = get_model_info("claude-3-5-haiku-20241022")
        assert info is not None
        assert info["supports_images"] is False


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
        assert len(models) == len(ANTHROPIC_MODELS)
        # Each model should have id and name (backward compatible)
        for m in models:
            assert "id" in m
            assert "name" in m
            # Should also carry metadata through
            assert "max_tokens" in m
            assert "description" in m

    def test_list_models_ids_match_registry(self):
        p = AnthropicProvider()
        models = p.list_models()
        ids = {m["id"] for m in models}
        assert ids == set(ANTHROPIC_MODELS.keys())

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

    def test_configure_prompt_cache_disabled(self):
        p = AnthropicProvider()
        p.configure(api_key="", prompt_cache_enabled=False)
        assert p._prompt_cache_enabled is False

    def test_configure_prompt_cache_default_enabled(self):
        p = AnthropicProvider()
        p.configure(api_key="")
        assert p._prompt_cache_enabled is True


# ---------------------------------------------------------------------------
# Prompt Caching
# ---------------------------------------------------------------------------

class TestAnthropicPromptCaching:
    """Verify prompt caching logic without making real API calls."""

    def setup_method(self):
        self.provider = AnthropicProvider()

    # -- _should_use_cache -------------------------------------------------

    def test_cache_enabled_for_supported_model(self):
        info = get_model_info("claude-sonnet-4-6")
        assert self.provider._should_use_cache(info) is True

    def test_cache_disabled_when_provider_flag_off(self):
        self.provider._prompt_cache_enabled = False
        info = get_model_info("claude-sonnet-4-6")
        assert self.provider._should_use_cache(info) is False

    def test_cache_disabled_for_unknown_model(self):
        assert self.provider._should_use_cache(None) is False

    # -- _prepare_system ---------------------------------------------------

    def test_prepare_system_no_cache(self):
        """Without caching, system prompt passes through unchanged."""
        result = AnthropicProvider._prepare_system("You are helpful.", False)
        assert result == "You are helpful."

    def test_prepare_system_with_cache_string(self):
        """String system prompt is converted to blocks with cache_control."""
        result = AnthropicProvider._prepare_system("You are helpful.", True)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "You are helpful."
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_prepare_system_with_cache_list(self):
        """List system prompt has cache_control added to last block."""
        system = [
            {"type": "text", "text": "Part one."},
            {"type": "text", "text": "Part two."},
        ]
        original = copy.deepcopy(system)
        result = AnthropicProvider._prepare_system(system, True)
        # Original should not be mutated
        assert system == original
        assert len(result) == 2
        assert "cache_control" not in result[0]
        assert result[1]["cache_control"] == {"type": "ephemeral"}

    def test_prepare_system_empty(self):
        assert AnthropicProvider._prepare_system("", True) == ""
        assert AnthropicProvider._prepare_system(None, True) is None

    # -- _prepare_messages -------------------------------------------------

    def test_prepare_messages_no_cache(self):
        """Without caching, messages pass through unchanged."""
        msgs = [{"role": "user", "content": "hello"}]
        result = AnthropicProvider._prepare_messages(msgs, False)
        assert result is msgs  # same reference, not copied

    def test_prepare_messages_empty(self):
        result = AnthropicProvider._prepare_messages([], True)
        assert result == []

    def test_prepare_messages_single_user_string(self):
        """Single user message with string content gets cache_control."""
        msgs = [{"role": "user", "content": "hello"}]
        result = AnthropicProvider._prepare_messages(msgs, True)
        # Should be deep-copied
        assert result is not msgs
        # String content should be converted to block form
        content = result[0]["content"]
        assert isinstance(content, list)
        assert content[0]["cache_control"] == {"type": "ephemeral"}
        assert content[0]["text"] == "hello"

    def test_prepare_messages_two_user_messages(self):
        """Last two user messages should both get cache_control."""
        msgs = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second question"},
        ]
        result = AnthropicProvider._prepare_messages(msgs, True)
        # Both user messages (index 0 and 2) should have cache_control
        assert result[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert result[2]["content"][0]["cache_control"] == {"type": "ephemeral"}
        # Assistant message should be unchanged
        assert result[1]["content"] == "first answer"

    def test_prepare_messages_user_block_content(self):
        """User message with list content gets cache_control on last block."""
        msgs = [
            {"role": "user", "content": [
                {"type": "text", "text": "block one"},
                {"type": "text", "text": "block two"},
            ]},
        ]
        original = copy.deepcopy(msgs)
        result = AnthropicProvider._prepare_messages(msgs, True)
        # Original should not be mutated
        assert msgs == original
        content = result[0]["content"]
        assert "cache_control" not in content[0]
        assert content[1]["cache_control"] == {"type": "ephemeral"}

    def test_prepare_messages_only_last_two_users(self):
        """With three user messages, only the last two get cache_control."""
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "third"},
        ]
        result = AnthropicProvider._prepare_messages(msgs, True)
        # First user (index 0) should NOT have cache_control
        assert isinstance(result[0]["content"], str)
        # Second user (index 2) should have cache_control
        assert result[2]["content"][0]["cache_control"] == {"type": "ephemeral"}
        # Third user (index 4) should have cache_control
        assert result[4]["content"][0]["cache_control"] == {"type": "ephemeral"}

    # -- _build_api_kwargs -------------------------------------------------

    def test_build_kwargs_with_cache(self):
        """When caching is active, extra_headers should include the beta header."""
        p = AnthropicProvider()
        kwargs = p._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="You are helpful.",
            tools=[],
            max_tokens=1024,
            model="claude-sonnet-4-6",
            use_cache=True,
        )
        assert "extra_headers" in kwargs
        assert "prompt-caching" in kwargs["extra_headers"]["anthropic-beta"]
        # System should be list with cache_control
        assert isinstance(kwargs["system"], list)
        # Tools should not be in kwargs when empty
        assert "tools" not in kwargs

    def test_build_kwargs_without_cache(self):
        """When caching is off, no extra_headers and system is plain string."""
        p = AnthropicProvider()
        kwargs = p._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="You are helpful.",
            tools=[],
            max_tokens=1024,
            model="claude-sonnet-4-6",
            use_cache=False,
        )
        assert "extra_headers" not in kwargs
        assert kwargs["system"] == "You are helpful."

    def test_build_kwargs_includes_tools_when_nonempty(self):
        p = AnthropicProvider()
        tools = [{"name": "test_tool", "description": "A test", "input_schema": {}}]
        kwargs = p._build_api_kwargs(
            messages=[], system="", tools=tools,
            max_tokens=1024, model="test", use_cache=False,
        )
        assert kwargs["tools"] == tools

    # -- _convert_response with cache stats --------------------------------

    def test_convert_response_with_cache_stats(self):
        """Cache usage fields should be extracted when use_cache=True."""
        p = AnthropicProvider()
        mock_response = MagicMock()
        mock_response.model = "claude-sonnet-4-6"
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_response.usage.cache_creation_input_tokens = 80
        mock_response.usage.cache_read_input_tokens = 20
        mock_response.content = []

        result = p._convert_response(mock_response, use_cache=True)
        assert result.usage["cache_creation_input_tokens"] == 80
        assert result.usage["cache_read_input_tokens"] == 20

    def test_convert_response_without_cache(self):
        """Without caching, no cache fields in usage."""
        p = AnthropicProvider()
        mock_response = MagicMock()
        mock_response.model = "claude-sonnet-4-6"
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_response.content = []

        result = p._convert_response(mock_response, use_cache=False)
        assert "cache_creation_input_tokens" not in result.usage
        assert "cache_read_input_tokens" not in result.usage


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

    def test_configure_api_key(self):
        p = OllamaProvider()
        p.configure(api_key="my-secret-key")
        assert p._api_key == "my-secret-key"
        assert p._auth_headers() == {"Authorization": "Bearer my-secret-key"}

    def test_configure_no_api_key(self):
        p = OllamaProvider()
        p.configure()
        assert p._api_key is None
        assert p._auth_headers() == {}

    def test_configure_num_ctx(self):
        p = OllamaProvider()
        p.configure(num_ctx=8192)
        assert p._num_ctx == 8192

    def test_configure_num_ctx_none(self):
        p = OllamaProvider()
        p.configure(num_ctx=None)
        assert p._num_ctx is None

    def test_list_models_delegates_to_discovery(self):
        """list_models should use the two-phase discovery pipeline."""
        p = OllamaProvider()
        fake_models = [
            {"id": "llama3.1:latest", "name": "llama3.1:latest", "supports_tools": True},
            {"id": "qwen2.5:latest", "name": "qwen2.5:latest", "supports_tools": False},
        ]
        with patch.object(p, "_get_models_cached", return_value=fake_models):
            models = p.list_models()
            assert len(models) == 2
            assert models[0]["id"] == "llama3.1:latest"
            assert models[1]["name"] == "qwen2.5:latest"

    def test_list_models_failure_returns_empty(self):
        """When discovery fails entirely, list_models returns []."""
        p = OllamaProvider()
        with patch.object(p, "_get_models_cached", return_value=[]):
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
        # Native format: arguments as dict, not JSON string
        assert msg["tool_calls"][0]["function"]["arguments"] == {"width": 10}
        # Native format: no "id" or "type" on tool_calls
        assert "id" not in msg["tool_calls"][0]
        assert "type" not in msg["tool_calls"][0]

    def test_tool_result_blocks(self):
        """Native format: tool results use positional correlation, no tool_call_id."""
        messages = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tc1",
                 "content": '{"success": true}'},
            ]},
        ]
        result = self.provider._convert_messages(messages, "")
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        # Native API does not use tool_call_id
        assert "tool_call_id" not in result[0]
        assert result[0]["content"] == '{"success": true}'

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
    """Test the native Ollama /api/chat -> LLMResponse conversion."""

    def setup_method(self):
        self.provider = OllamaProvider()

    def test_text_response(self):
        data = {
            "model": "llama3.1",
            "message": {"role": "assistant", "content": "Hello!"},
            "done": True,
            "prompt_eval_count": 10,
            "eval_count": 5,
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
        """Native API returns arguments as a dict, not a JSON string."""
        data = {
            "model": "llama3.1",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "create_box",
                        "arguments": {"width": 10},
                    },
                }],
            },
            "done": True,
            "prompt_eval_count": 20,
            "eval_count": 15,
        }
        result = self.provider._convert_response(data)
        assert result.stop_reason == "tool_use"
        assert len(result.content) == 1
        assert result.content[0]["type"] == "tool_use"
        assert result.content[0]["name"] == "create_box"
        assert result.content[0]["input"] == {"width": 10}
        # Synthetic tool_use ID should be generated
        assert result.content[0]["id"].startswith("toolu_")

    def test_empty_message(self):
        data = {"model": "llama3.1", "message": {"role": "assistant"}, "done": True}
        result = self.provider._convert_response(data)
        assert result.content == []
        assert result.stop_reason == "end_turn"

    def test_thinking_field_extracted(self):
        """Native thinking field should be extracted as reasoning."""
        data = {
            "model": "qwen3:8b",
            "message": {
                "role": "assistant",
                "content": "The answer is 42.",
                "thinking": "Let me reason step by step...",
            },
            "done": True,
            "prompt_eval_count": 50,
            "eval_count": 20,
        }
        result = self.provider._convert_response(data)
        assert result.reasoning == "Let me reason step by step..."
        assert len(result.content) == 1
        assert result.content[0]["text"] == "The answer is 42."

    def test_tool_call_arguments_as_json_string_fallback(self):
        """If arguments are somehow a JSON string, they should be parsed."""
        data = {
            "model": "llama3.1",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "get_info",
                        "arguments": '{"key": "value"}',
                    },
                }],
            },
            "done": True,
        }
        result = self.provider._convert_response(data)
        assert result.content[0]["input"] == {"key": "value"}

    @patch("ai.providers.ollama_provider.requests.post")
    def test_create_message(self, mock_post):
        """Test that create_message calls the native /api/chat endpoint.

        TASK-240: _resolve_num_ctx() may make an /api/show call to detect
        the context window, so we expect 2 POST calls: /api/show + /api/chat.
        """
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "model": "llama3.1",
            "message": {"role": "assistant", "content": "Done!"},
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 3,
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
        # TASK-240: _resolve_num_ctx may call /api/show first, then /api/chat
        assert mock_post.call_count >= 1
        # Find the /api/chat call specifically
        chat_calls = [c for c in mock_post.call_args_list if "/api/chat" in str(c)]
        assert len(chat_calls) == 1, f"Expected exactly 1 /api/chat call, got {len(chat_calls)}"
        call_args = chat_calls[0]
        assert "/api/chat" in call_args[0][0]
        # Should NOT be the OpenAI compat endpoint
        assert "/v1/" not in call_args[0][0]
        # TASK-240: Verify num_ctx is always sent in options
        payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1] if len(call_args[0]) > 1 else {}
        if "options" in payload:
            assert "num_ctx" in payload["options"], "num_ctx must always be sent to Ollama"
            assert payload["options"]["num_ctx"] > 0, "num_ctx must be positive"


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
        # Default active is anthropic -> returns list derived from ANTHROPIC_MODELS
        assert isinstance(models, list)
        assert len(models) == len(ANTHROPIC_MODELS)
        ids = {m["id"] for m in models}
        assert ids == set(ANTHROPIC_MODELS.keys())

    def test_list_models_for_specific_provider(self):
        pm = ProviderManager()
        # Ollama list_models will likely fail (no server), returns []
        # but we can mock it
        with patch.object(pm.get_provider("ollama"), "list_models", return_value=[{"id": "m1", "name": "m1"}]):
            models = pm.list_models("ollama")
            assert len(models) == 1
            assert models[0]["id"] == "m1"


# ---------------------------------------------------------------------------
# OllamaProvider -- HTTP error handling
# ---------------------------------------------------------------------------

class TestOllama404Handling:
    """Verify descriptive errors for HTTP 404 and other status codes."""

    @patch("ai.providers.ollama_provider.requests.post")
    def test_create_message_404_raises_descriptive_error(self, mock_post):
        """HTTP 404 should raise RuntimeError mentioning 'ollama pull'."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "model not found"
        http_err = __import__("requests").exceptions.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        with pytest.raises(RuntimeError, match="ollama pull"):
            p.create_message(
                messages=[{"role": "user", "content": "hi"}],
                system="",
                tools=[],
                max_tokens=100,
                model="nonexistent-model",
            )

    @patch("ai.providers.ollama_provider.requests.post")
    def test_create_message_500_raises_http_error(self, mock_post):
        """HTTP 500 should raise RuntimeError with status code."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "internal server error"
        http_err = __import__("requests").exceptions.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        with pytest.raises(RuntimeError, match="500"):
            p.create_message(
                messages=[{"role": "user", "content": "hi"}],
                system="",
                tools=[],
                max_tokens=100,
                model="llama3.1",
            )

    @patch("ai.providers.ollama_provider.requests.post")
    def test_stream_message_404_raises_descriptive_error(self, mock_post):
        """Streaming with HTTP 404 should raise RuntimeError mentioning 'ollama pull'."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "model not found"
        http_err = __import__("requests").exceptions.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        with pytest.raises(RuntimeError, match="ollama pull"):
            p.stream_message(
                messages=[{"role": "user", "content": "hi"}],
                system="",
                tools=[],
                max_tokens=100,
                model="nonexistent-model",
            )

    @patch("ai.providers.ollama_provider.requests.post")
    def test_stream_message_other_http_error(self, mock_post):
        """Streaming with HTTP 502 should raise descriptive RuntimeError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.text = "bad gateway"
        http_err = __import__("requests").exceptions.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        with pytest.raises(RuntimeError, match="502"):
            p.stream_message(
                messages=[{"role": "user", "content": "hi"}],
                system="",
                tools=[],
                max_tokens=100,
                model="llama3.1",
            )


# ---------------------------------------------------------------------------
# OllamaProvider -- Model Discovery
# ---------------------------------------------------------------------------

class TestOllamaModelDiscovery:
    """Test the two-phase model discovery pipeline."""

    def _make_provider(self) -> OllamaProvider:
        """Create a provider with SDK disabled for predictable HTTP testing."""
        p = OllamaProvider()
        p._sdk_client = None  # Force HTTP path
        return p

    @patch("ai.providers.ollama_provider.requests.post")
    @patch("ai.providers.ollama_provider.requests.get")
    def test_discover_models_two_phase(self, mock_get, mock_post):
        """Phase 1 lists models; Phase 2 fetches metadata per model."""
        # Phase 1: /api/tags
        tags_resp = MagicMock()
        tags_resp.status_code = 200
        tags_resp.json.return_value = {
            "models": [
                {"name": "llama3.1:latest", "size": 4_000_000_000, "modified_at": "2024-01-01"},
            ]
        }
        mock_get.return_value = tags_resp

        # Phase 2: /api/show
        show_resp = MagicMock()
        show_resp.status_code = 200
        show_resp.json.return_value = {
            "model_info": {"general.context_length": 8192},
            "details": {"parameter_size": "8B", "family": "llama"},
            "capabilities": ["completion", "tools"],
        }
        mock_post.return_value = show_resp

        p = self._make_provider()
        models = p._discover_models()

        assert len(models) == 1
        m = models[0]
        assert m["id"] == "llama3.1:latest"
        assert m["context_length"] == 8192
        assert m["supports_tools"] is True
        assert m["supports_vision"] is False
        assert m["parameter_size"] == "8B"
        assert m["family"] == "llama"

    @patch("ai.providers.ollama_provider.requests.post")
    @patch("ai.providers.ollama_provider.requests.get")
    def test_discover_models_with_vision(self, mock_get, mock_post):
        """Models with vision capability should have supports_vision=True."""
        tags_resp = MagicMock()
        tags_resp.status_code = 200
        tags_resp.json.return_value = {
            "models": [{"name": "llava:latest", "size": 5_000_000_000}]
        }
        mock_get.return_value = tags_resp

        show_resp = MagicMock()
        show_resp.status_code = 200
        show_resp.json.return_value = {
            "model_info": {},
            "details": {"parameter_size": "7B", "family": "llava"},
            "capabilities": ["completion", "vision"],
        }
        mock_post.return_value = show_resp

        p = self._make_provider()
        models = p._discover_models()
        assert models[0]["supports_vision"] is True
        assert models[0]["supports_tools"] is False

    @patch("ai.providers.ollama_provider.requests.get")
    def test_discover_models_empty_api(self, mock_get):
        """Empty model list from API returns []."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"models": []}
        mock_get.return_value = resp

        p = self._make_provider()
        assert p._discover_models() == []

    @patch("ai.providers.ollama_provider.requests.get")
    def test_discover_models_api_failure(self, mock_get):
        """HTTP failure in Phase 1 returns []."""
        mock_get.side_effect = ConnectionError("refused")
        p = self._make_provider()
        assert p._discover_models() == []

    @patch("ai.providers.ollama_provider.requests.post")
    @patch("ai.providers.ollama_provider.requests.get")
    def test_discover_models_show_failure_fallback(self, mock_get, mock_post):
        """If /api/show fails for a model, it still appears with defaults."""
        tags_resp = MagicMock()
        tags_resp.status_code = 200
        tags_resp.json.return_value = {
            "models": [{"name": "mystery:latest", "size": 1000}]
        }
        mock_get.return_value = tags_resp
        mock_post.side_effect = ConnectionError("timeout")

        p = self._make_provider()
        models = p._discover_models()
        assert len(models) == 1
        assert models[0]["supports_tools"] is False
        assert models[0]["description"] == "mystery:latest"


# ---------------------------------------------------------------------------
# OllamaProvider -- Tool-Capability Filtering
# ---------------------------------------------------------------------------

class TestOllamaToolFiltering:
    """Test list_models with tool_capable_only filter."""

    def test_filter_tool_capable_only(self):
        p = OllamaProvider()
        fake_models = [
            {"id": "toolmodel", "supports_tools": True},
            {"id": "notoolmodel", "supports_tools": False},
            {"id": "toolmodel2", "supports_tools": True},
        ]
        with patch.object(p, "_get_models_cached", return_value=fake_models):
            filtered = p.list_models(tool_capable_only=True)
            assert len(filtered) == 2
            assert all(m["supports_tools"] for m in filtered)

    def test_no_filter_returns_all(self):
        p = OllamaProvider()
        fake_models = [
            {"id": "a", "supports_tools": True},
            {"id": "b", "supports_tools": False},
        ]
        with patch.object(p, "_get_models_cached", return_value=fake_models):
            all_models = p.list_models(tool_capable_only=False)
            assert len(all_models) == 2

    def test_filter_no_tool_models(self):
        p = OllamaProvider()
        fake_models = [
            {"id": "basic", "supports_tools": False},
        ]
        with patch.object(p, "_get_models_cached", return_value=fake_models):
            filtered = p.list_models(tool_capable_only=True)
            assert filtered == []


# ---------------------------------------------------------------------------
# OllamaProvider -- Two-Tier Cache
# ---------------------------------------------------------------------------

class TestOllamaModelCache:
    """Test the memory and disk caching for model discovery."""

    def test_memory_cache_hit(self):
        """If memory cache is fresh, no API call is made."""
        p = OllamaProvider()
        cached_models = [{"id": "cached-model", "supports_tools": True}]
        p._model_cache = cached_models
        p._model_cache_time = time.time()  # Fresh

        with patch.object(p, "_discover_models") as mock_discover:
            result = p._get_models_cached()
            mock_discover.assert_not_called()
            assert result == cached_models

    def test_memory_cache_expired_triggers_api(self):
        """Expired memory cache should trigger API discovery."""
        p = OllamaProvider()
        p._model_cache = [{"id": "stale"}]
        p._model_cache_time = time.time() - _MODEL_CACHE_TTL - 10  # Expired

        fresh_models = [{"id": "fresh-model", "supports_tools": True}]
        with patch.object(p, "_read_disk_cache", return_value=None), \
             patch.object(p, "_discover_models", return_value=fresh_models), \
             patch.object(p, "_write_disk_cache"):
            result = p._get_models_cached()
            assert result == fresh_models

    def test_empty_api_response_does_not_overwrite_cache(self):
        """Empty API response should not overwrite existing good cache."""
        p = OllamaProvider()
        good_cache = [{"id": "good-model", "supports_tools": True}]
        p._model_cache = good_cache
        p._model_cache_time = time.time() - _MODEL_CACHE_TTL - 10  # Expired

        with patch.object(p, "_read_disk_cache", return_value=None), \
             patch.object(p, "_discover_models", return_value=[]), \
             patch.object(p, "_write_disk_cache") as mock_write:
            result = p._get_models_cached()
            # Should fall back to existing memory cache
            assert result == good_cache
            mock_write.assert_not_called()

    def test_disk_cache_read_write(self, tmp_path):
        """Test disk cache write and read round-trip."""
        import ai.providers.ollama_provider as mod
        original = mod._DISK_CACHE_FILE

        cache_file = str(tmp_path / "test_cache.json")
        mod._DISK_CACHE_FILE = cache_file
        try:
            p = OllamaProvider()
            models = [{"id": "test-model", "supports_tools": True, "name": "test"}]

            p._write_disk_cache(models)
            assert os.path.exists(cache_file)

            loaded = p._read_disk_cache()
            assert loaded is not None
            assert len(loaded) == 1
            assert loaded[0]["id"] == "test-model"
        finally:
            mod._DISK_CACHE_FILE = original

    def test_disk_cache_missing_returns_none(self, tmp_path):
        """Missing disk cache file returns None."""
        import ai.providers.ollama_provider as mod
        original = mod._DISK_CACHE_FILE
        mod._DISK_CACHE_FILE = str(tmp_path / "nonexistent.json")
        try:
            p = OllamaProvider()
            assert p._read_disk_cache() is None
        finally:
            mod._DISK_CACHE_FILE = original


# ---------------------------------------------------------------------------
# OllamaProvider -- DeepSeek R1 Detection
# ---------------------------------------------------------------------------

class TestDeepSeekR1Detection:
    """Test DeepSeek R1 model detection and reasoning parsing."""

    def test_is_deepseek_r1_positive(self):
        assert OllamaProvider._is_deepseek_r1("deepseek-r1:32b") is True
        assert OllamaProvider._is_deepseek_r1("DeepSeek-R1:70b") is True
        assert OllamaProvider._is_deepseek_r1("some/deepseek-r1-distill") is True

    def test_is_deepseek_r1_negative(self):
        assert OllamaProvider._is_deepseek_r1("llama3.1") is False
        assert OllamaProvider._is_deepseek_r1("deepseek-v2") is False

    def test_parse_r1_content_with_think_block(self):
        text = "<think>Let me reason about this...</think>The answer is 42."
        blocks = OllamaProvider._parse_r1_content(text)
        assert len(blocks) == 2
        assert blocks[0]["type"] == "reasoning"
        assert blocks[0]["text"] == "Let me reason about this..."
        assert blocks[1]["type"] == "text"
        assert blocks[1]["text"] == "The answer is 42."

    def test_parse_r1_content_multiple_think_blocks(self):
        text = "<think>Step 1</think>Middle text<think>Step 2</think>Final."
        blocks = OllamaProvider._parse_r1_content(text)
        assert len(blocks) == 4
        assert blocks[0]["type"] == "reasoning"
        assert blocks[1]["type"] == "text"
        assert blocks[2]["type"] == "reasoning"
        assert blocks[3]["type"] == "text"

    def test_parse_r1_content_no_think_blocks(self):
        text = "Just plain text, no reasoning."
        blocks = OllamaProvider._parse_r1_content(text)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert blocks[0]["text"] == text

    def test_parse_r1_content_empty_think_block(self):
        text = "<think></think>Some answer."
        blocks = OllamaProvider._parse_r1_content(text)
        # Empty think block is skipped; only the text remains
        assert any(b["type"] == "text" for b in blocks)

    @patch("ai.providers.ollama_provider.requests.post")
    def test_create_message_deepseek_r1_sets_temperature(self, mock_post):
        """DeepSeek R1 models should have temperature set to 0.6 via options."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "model": "deepseek-r1:32b",
            "message": {"role": "assistant", "content": "<think>Reasoning</think>Answer"},
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 3,
        }
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        result = p.create_message(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=100,
            model="deepseek-r1:32b",
        )

        # Verify temperature was set in options (native format)
        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs[0][1]
        assert payload["options"]["temperature"] == _DEEPSEEK_R1_TEMPERATURE

        # Verify reasoning blocks were parsed
        reasoning_blocks = [b for b in result.content if b["type"] == "reasoning"]
        assert len(reasoning_blocks) == 1


# ---------------------------------------------------------------------------
# OllamaProvider -- num_ctx and Auth in API calls
# ---------------------------------------------------------------------------

class TestOllamaNumCtxAndAuth:
    """Test that num_ctx and auth headers are sent in API calls."""

    @patch("ai.providers.ollama_provider.requests.post")
    def test_num_ctx_included_when_configured(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "model": "llama3.1",
            "message": {"role": "assistant", "content": "OK"},
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 1,
        }
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        p._num_ctx = 16384
        p.create_message(
            messages=[{"role": "user", "content": "hi"}],
            system="", tools=[], max_tokens=100, model="llama3.1",
        )

        payload = mock_post.call_args[1]["json"]
        assert payload["options"]["num_ctx"] == 16384

    @patch("ai.providers.ollama_provider.requests.post")
    def test_num_ctx_always_sent_even_when_none(self, mock_post):
        """TASK-240: num_ctx must ALWAYS be sent to prevent Ollama from
        falling back to a tiny Modelfile default.  When _num_ctx is None,
        _resolve_num_ctx() should still produce a positive floor value.
        """
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "model": "llama3.1",
            "message": {"role": "assistant", "content": "OK"},
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 1,
        }
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        p._num_ctx = None
        p.create_message(
            messages=[{"role": "user", "content": "hi"}],
            system="", tools=[], max_tokens=100, model="llama3.1",
        )

        # Find the /api/chat call (there may also be an /api/show call)
        chat_calls = [c for c in mock_post.call_args_list if "/api/chat" in str(c)]
        assert len(chat_calls) == 1
        payload = chat_calls[0][1]["json"]
        # TASK-240: options with num_ctx must always be present
        assert "options" in payload, "options must always be sent to Ollama"
        assert "num_ctx" in payload["options"], "num_ctx must always be in options"
        assert payload["options"]["num_ctx"] > 0, "num_ctx must be positive"

    @patch("ai.providers.ollama_provider.requests.post")
    def test_auth_header_included_when_key_set(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "model": "llama3.1",
            "message": {"role": "assistant", "content": "OK"},
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 1,
        }
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        p._api_key = "test-key-123"
        p.create_message(
            messages=[{"role": "user", "content": "hi"}],
            system="", tools=[], max_tokens=100, model="llama3.1",
        )

        headers = mock_post.call_args[1]["headers"]
        assert headers == {"Authorization": "Bearer test-key-123"}


# ---------------------------------------------------------------------------
# OllamaProvider -- Default Model Configuration
# ---------------------------------------------------------------------------

class TestOllamaDefaultModelConfig:
    """Test the default model ID and info constants."""

    def test_default_model_id(self):
        assert OLLAMA_DEFAULT_MODEL_ID == "devstral:24b"

    def test_default_model_info_has_required_keys(self):
        required = {"max_tokens", "context_window", "supports_images",
                     "supports_tools", "input_price", "output_price"}
        assert required.issubset(set(OLLAMA_DEFAULT_MODEL_INFO.keys()))

    def test_default_model_info_free(self):
        assert OLLAMA_DEFAULT_MODEL_INFO["input_price"] == 0
        assert OLLAMA_DEFAULT_MODEL_INFO["output_price"] == 0

    def test_default_model_supports_tools(self):
        assert OLLAMA_DEFAULT_MODEL_INFO["supports_tools"] is True


# ---------------------------------------------------------------------------
# BaseProvider -- clamp_max_tokens
# ---------------------------------------------------------------------------

class TestClampMaxTokens:
    """Verify the static max_tokens clamping utility."""

    def test_non_reasoning_caps_at_50_percent(self):
        """Non-reasoning: min(max_tokens, int(context_window * 0.5)) when no max_output."""
        # 200k context -> ceiling = 100_000; request 120_000 -> clamped to 100_000
        result = BaseProvider.clamp_max_tokens(120_000, 200_000, is_reasoning=False)
        assert result == 100_000

    def test_non_reasoning_caps_at_max_output(self):
        """Non-reasoning: when max_output is provided, it serves as ceiling."""
        # max_output = 64_000; request 80_000 -> clamped to 64_000
        result = BaseProvider.clamp_max_tokens(80_000, 200_000, is_reasoning=False, max_output=64_000)
        assert result == 64_000

    def test_non_reasoning_under_cap(self):
        """When requested tokens are below the cap, return requested (above floor)."""
        result = BaseProvider.clamp_max_tokens(16_000, 200_000, is_reasoning=False)
        assert result == 16_000

    def test_non_reasoning_floor_enforcement(self):
        """Floor of 8192 is enforced even when the 50% cap is lower."""
        # 10k context -> cap = 5000; but floor = 8192
        result = BaseProvider.clamp_max_tokens(1000, 10_000, is_reasoning=False)
        assert result == _ANTHROPIC_MIN_MAX_TOKENS

    def test_reasoning_uses_provided_value(self):
        """Reasoning models use provided max_tokens (if above floor)."""
        result = BaseProvider.clamp_max_tokens(32_000, 200_000, is_reasoning=True)
        assert result == 32_000

    def test_reasoning_defaults_when_zero(self):
        """Reasoning with max_tokens=0 defaults to 16384."""
        result = BaseProvider.clamp_max_tokens(0, 200_000, is_reasoning=True)
        assert result == 16_384

    def test_reasoning_floor_enforcement(self):
        """Reasoning floor: never below 8192."""
        result = BaseProvider.clamp_max_tokens(4000, 200_000, is_reasoning=True)
        assert result == _ANTHROPIC_MIN_MAX_TOKENS

    def test_various_context_sizes(self):
        """Non-reasoning clamping across different context windows."""
        # 128k context -> cap = 64000; request 30k -> returns 30k (under cap)
        assert BaseProvider.clamp_max_tokens(30_000, 128_000) == 30_000
        # 32k context -> cap = 16000; request 30k -> clamped to 16000
        assert BaseProvider.clamp_max_tokens(30_000, 32_000) == 16_000


# ---------------------------------------------------------------------------
# AnthropicProvider -- Reasoning Budget (Extended Thinking)
# ---------------------------------------------------------------------------

class TestAnthropicReasoning:
    """Verify reasoning budget / extended thinking logic."""

    def setup_method(self):
        self.provider = AnthropicProvider()

    # -- _resolve_model ----------------------------------------------------

    def test_resolve_model_no_suffix(self):
        model, suffix = AnthropicProvider._resolve_model("claude-sonnet-4-6")
        assert model == "claude-sonnet-4-6"
        assert suffix is False

    def test_resolve_model_thinking_suffix(self):
        model, suffix = AnthropicProvider._resolve_model(
            "claude-3-7-sonnet-20250219:thinking"
        )
        assert model == "claude-3-7-sonnet-20250219"
        assert suffix is True

    # -- _should_use_reasoning ---------------------------------------------

    def test_reasoning_disabled_by_default(self):
        """Reasoning is off when provider toggle is False and model is optional."""
        info = get_model_info("claude-sonnet-4-6")
        assert self.provider._should_use_reasoning(info) is False

    def test_reasoning_enabled_via_toggle(self):
        """When provider toggle is True and model supports reasoning."""
        self.provider._reasoning_enabled = True
        info = get_model_info("claude-sonnet-4-6")
        assert self.provider._should_use_reasoning(info) is True

    def test_reasoning_enabled_not_supported_by_model(self):
        """Model without supports_reasoning_budget should not enable reasoning."""
        self.provider._reasoning_enabled = True
        info = get_model_info("claude-3-5-sonnet-20241022")
        assert self.provider._should_use_reasoning(info) is False

    def test_reasoning_required_always_enabled(self):
        """Models with reasoning_required=True always use reasoning."""
        info = get_model_info("claude-3-7-sonnet-20250219:thinking")
        # Provider toggle is False, but reasoning_required overrides.
        assert self.provider._should_use_reasoning(info) is True

    def test_reasoning_with_thinking_suffix(self):
        """The :thinking suffix forces reasoning regardless of config toggle."""
        info = get_model_info("claude-3-7-sonnet-20250219")
        assert self.provider._should_use_reasoning(info, thinking_suffix=True) is True

    def test_reasoning_unknown_model(self):
        """Unknown model should not enable reasoning even if toggle is True."""
        self.provider._reasoning_enabled = True
        assert self.provider._should_use_reasoning(None) is False

    # -- Budget capping at 80% of max_tokens --------------------------------

    def test_budget_capped_at_80_percent(self):
        """Reasoning budget should not exceed 80% of model's max_tokens."""
        self.provider._reasoning_enabled = True
        self.provider._reasoning_budget = 100_000  # Very large budget
        info = get_model_info("claude-sonnet-4-6")  # max_tokens=64000
        kwargs = self.provider._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=8192,
            model="claude-sonnet-4-6",
            model_info=info,
            use_cache=False,
            use_reasoning=True,
        )
        thinking = kwargs["thinking"]
        assert thinking["type"] == "enabled"
        # 80% of 64000 = 51200
        assert thinking["budget_tokens"] == 51_200

    def test_budget_used_when_under_cap(self):
        """Budget is used as-is when it's within the 80% cap."""
        self.provider._reasoning_enabled = True
        self.provider._reasoning_budget = 8192
        info = get_model_info("claude-sonnet-4-6")  # max_tokens=64000 -> cap=51200
        kwargs = self.provider._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=8192,
            model="claude-sonnet-4-6",
            model_info=info,
            use_cache=False,
            use_reasoning=True,
        )
        assert kwargs["thinking"]["budget_tokens"] == 8192

    # -- Temperature forcing -----------------------------------------------

    def test_temperature_forced_to_1_when_reasoning(self):
        """Temperature must be 1.0 when reasoning is active."""
        self.provider._reasoning_enabled = True
        self.provider._reasoning_budget = 8192
        info = get_model_info("claude-sonnet-4-6")
        kwargs = self.provider._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=8192,
            model="claude-sonnet-4-6",
            model_info=info,
            use_cache=False,
            use_reasoning=True,
        )
        assert kwargs["temperature"] == 1.0

    def test_no_temperature_when_reasoning_off(self):
        """Temperature should not be set when reasoning is off."""
        info = get_model_info("claude-sonnet-4-6")
        kwargs = self.provider._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=8192,
            model="claude-sonnet-4-6",
            model_info=info,
            use_cache=False,
            use_reasoning=False,
        )
        assert "temperature" not in kwargs

    # -- :thinking suffix handling -----------------------------------------

    def test_thinking_suffix_adds_beta_header(self):
        """The :thinking suffix should add the output-128k beta header."""
        self.provider._reasoning_budget = 8192
        info = get_model_info("claude-3-7-sonnet-20250219:thinking")
        kwargs = self.provider._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=8192,
            model="claude-3-7-sonnet-20250219",
            model_info=info,
            use_cache=False,
            use_reasoning=True,
            thinking_suffix=True,
        )
        assert "extra_headers" in kwargs
        assert _OUTPUT_128K_BETA in kwargs["extra_headers"]["anthropic-beta"]

    def test_thinking_suffix_strips_model_id(self):
        """The :thinking suffix should be stripped from the model sent to the API."""
        model, suffix = AnthropicProvider._resolve_model(
            "claude-3-7-sonnet-20250219:thinking"
        )
        assert model == "claude-3-7-sonnet-20250219"
        assert suffix is True

    def test_thinking_suffix_combined_with_cache_headers(self):
        """Both cache and thinking beta headers should be present."""
        self.provider._reasoning_budget = 8192
        info = get_model_info("claude-3-7-sonnet-20250219:thinking")
        kwargs = self.provider._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=8192,
            model="claude-3-7-sonnet-20250219",
            model_info=info,
            use_cache=True,
            use_reasoning=True,
            thinking_suffix=True,
        )
        beta = kwargs["extra_headers"]["anthropic-beta"]
        assert "prompt-caching" in beta
        assert _OUTPUT_128K_BETA in beta

    # -- Reasoning content parsing -----------------------------------------

    def test_convert_response_with_thinking_blocks(self):
        """Thinking blocks should be extracted into a reasoning key."""
        p = AnthropicProvider()
        mock_response = MagicMock()
        mock_response.model = "claude-sonnet-4-6"
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        thinking_block = MagicMock()
        thinking_block.type = "thinking"
        thinking_block.thinking = "Let me think about this step by step..."

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "The answer is 42."

        mock_response.content = [thinking_block, text_block]

        result = p._convert_response(mock_response, use_cache=False)
        assert hasattr(result, "reasoning")
        assert result.reasoning == "Let me think about this step by step..."
        assert len(result.content) == 1
        assert result.content[0]["text"] == "The answer is 42."

    def test_convert_response_no_thinking_blocks(self):
        """Without thinking blocks, no reasoning attribute should be set."""
        p = AnthropicProvider()
        mock_response = MagicMock()
        mock_response.model = "claude-sonnet-4-6"
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Just a normal response."

        mock_response.content = [text_block]

        result = p._convert_response(mock_response, use_cache=False)
        # TASK-140: reasoning is now always initialized (to None when unused)
        assert result.reasoning is None
        assert len(result.content) == 1

    def test_convert_response_multiple_thinking_blocks(self):
        """Multiple thinking blocks should be joined with double newline."""
        p = AnthropicProvider()
        mock_response = MagicMock()
        mock_response.model = "claude-sonnet-4-6"
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        think1 = MagicMock()
        think1.type = "thinking"
        think1.thinking = "First thought."

        think2 = MagicMock()
        think2.type = "thinking"
        think2.thinking = "Second thought."

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Final answer."

        mock_response.content = [think1, think2, text_block]

        result = p._convert_response(mock_response, use_cache=False)
        assert result.reasoning == "First thought.\n\nSecond thought."


# ---------------------------------------------------------------------------
# AnthropicProvider -- Max Token Clamping Integration
# ---------------------------------------------------------------------------

class TestAnthropicMaxTokenClamping:
    """Verify max_tokens clamping is applied when building API kwargs."""

    def test_clamping_applied_in_build_kwargs(self):
        """max_tokens should be clamped based on model max_output_tokens."""
        p = AnthropicProvider()
        info = get_model_info("claude-sonnet-4-6")  # max_tokens=64000 in registry
        kwargs = p._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=100_000,  # Above model's 64k max_output
            model="claude-sonnet-4-6",
            model_info=info,
            use_cache=False,
        )
        assert kwargs["max_tokens"] == 64_000

    def test_clamping_reasoning_preserves_value(self):
        """Reasoning models should keep their max_tokens (with floor)."""
        p = AnthropicProvider()
        p._reasoning_enabled = True
        p._reasoning_budget = 8192
        info = get_model_info("claude-sonnet-4-6")
        kwargs = p._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=32_000,
            model="claude-sonnet-4-6",
            model_info=info,
            use_cache=False,
            use_reasoning=True,
        )
        assert kwargs["max_tokens"] == 32_000

    def test_clamping_floor_never_below_8192(self):
        """Even with a tiny request, floor of 8192 is enforced."""
        p = AnthropicProvider()
        info = get_model_info("claude-sonnet-4-6")
        kwargs = p._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=1000,
            model="claude-sonnet-4-6",
            model_info=info,
            use_cache=False,
        )
        assert kwargs["max_tokens"] == _ANTHROPIC_MIN_MAX_TOKENS

    def test_clamping_unknown_model_uses_default_context(self):
        """Unknown model defaults to 200k context_window, 50% ceiling."""
        p = AnthropicProvider()
        kwargs = p._build_api_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=50_000,
            model="unknown-model",
            model_info=None,
            use_cache=False,
        )
        # 50% of 200k = 100k; 50k is under that, so stays 50k
        assert kwargs["max_tokens"] == 50_000


# ---------------------------------------------------------------------------
# AnthropicProvider -- configure() reasoning settings
# ---------------------------------------------------------------------------

class TestAnthropicConfigureReasoning:
    """Verify that configure() picks up reasoning settings."""

    def test_configure_reasoning_defaults_from_settings(self):
        """configure() should read reasoning defaults from settings module."""
        p = AnthropicProvider()
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.anthropic_reasoning_enabled = False
            mock_settings.anthropic_reasoning_budget = 8192
            p.configure(api_key="")
            assert p._reasoning_enabled is False
            assert p._reasoning_budget == 8192

    def test_configure_reasoning_explicit_override(self):
        """Explicit kwargs should override settings defaults."""
        p = AnthropicProvider()
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.anthropic_reasoning_enabled = False
            mock_settings.anthropic_reasoning_budget = 8192
            p.configure(api_key="", reasoning_enabled=True, reasoning_budget=16384)
            assert p._reasoning_enabled is True
            assert p._reasoning_budget == 16384


# ---------------------------------------------------------------------------
# Anthropic 1M Extended Context Beta
# ---------------------------------------------------------------------------

class TestAnthropic1MContextFlag:
    """Verify supports_1m_context flag on correct models."""

    _1M_MODELS = {
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-sonnet-4-20250514",
        "claude-opus-4-6",
    }

    def test_1m_supported_models_have_flag(self):
        for model_id in self._1M_MODELS:
            info = get_model_info(model_id)
            assert info is not None, f"{model_id} not in registry"
            assert info.get("supports_1m_context") is True, (
                f"{model_id} should have supports_1m_context=True"
            )

    def test_other_models_lack_flag(self):
        for model_id, meta in ANTHROPIC_MODELS.items():
            if model_id not in self._1M_MODELS:
                assert not meta.get("supports_1m_context", False), (
                    f"{model_id} should not have supports_1m_context=True"
                )


class TestAnthropic1MContextBetaHeader:
    """Verify 1M beta header added/omitted based on settings."""

    def test_1m_header_added_when_enabled(self):
        """When 1M is enabled and model supports it, the beta header is present."""
        p = AnthropicProvider()
        info = get_model_info("claude-sonnet-4-6")
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.get.return_value = True  # anthropic_1m_context_enabled
            kwargs = p._build_api_kwargs(
                messages=[{"role": "user", "content": "hi"}],
                system="test",
                tools=[],
                max_tokens=1024,
                model="claude-sonnet-4-6",
                model_info=info,
                use_cache=False,
            )
            assert "extra_headers" in kwargs
            assert _CONTEXT_1M_BETA in kwargs["extra_headers"]["anthropic-beta"]

    def test_1m_header_not_added_when_disabled(self):
        """When 1M is disabled, no 1M beta header."""
        p = AnthropicProvider()
        info = get_model_info("claude-sonnet-4-6")
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.get.return_value = False
            kwargs = p._build_api_kwargs(
                messages=[{"role": "user", "content": "hi"}],
                system="test",
                tools=[],
                max_tokens=1024,
                model="claude-sonnet-4-6",
                model_info=info,
                use_cache=False,
            )
            # Either no extra_headers at all, or 1M beta not in them
            if "extra_headers" in kwargs:
                assert _CONTEXT_1M_BETA not in kwargs["extra_headers"].get("anthropic-beta", "")

    def test_1m_header_not_added_for_unsupported_model(self):
        """Even when 1M is enabled, unsupported models don't get the header."""
        p = AnthropicProvider()
        info = get_model_info("claude-3-5-haiku-20241022")
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.get.return_value = True
            kwargs = p._build_api_kwargs(
                messages=[{"role": "user", "content": "hi"}],
                system="test",
                tools=[],
                max_tokens=1024,
                model="claude-3-5-haiku-20241022",
                model_info=info,
                use_cache=False,
            )
            if "extra_headers" in kwargs:
                assert _CONTEXT_1M_BETA not in kwargs["extra_headers"].get("anthropic-beta", "")

    def test_1m_header_combined_with_cache_and_thinking(self):
        """1M header coexists with cache and thinking beta headers."""
        p = AnthropicProvider()
        p._reasoning_budget = 8192
        info = get_model_info("claude-sonnet-4-6")
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.get.return_value = True
            kwargs = p._build_api_kwargs(
                messages=[{"role": "user", "content": "hi"}],
                system="test",
                tools=[],
                max_tokens=8192,
                model="claude-sonnet-4-6",
                model_info=info,
                use_cache=True,
                use_reasoning=True,
                thinking_suffix=True,
            )
            beta = kwargs["extra_headers"]["anthropic-beta"]
            assert "prompt-caching" in beta
            assert _OUTPUT_128K_BETA in beta
            assert _CONTEXT_1M_BETA in beta


class TestGetEffectiveContextWindow:
    """Verify get_effective_context_window returns correct values."""

    def test_returns_200k_when_disabled(self):
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.get.return_value = False
            result = get_effective_context_window("claude-sonnet-4-6")
            assert result == 200_000

    def test_returns_1m_when_enabled_for_supported_model(self):
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.get.return_value = True
            result = get_effective_context_window("claude-sonnet-4-6")
            assert result == 1_000_000

    def test_returns_1m_for_all_supported_models(self):
        supported = ["claude-sonnet-4-6", "claude-sonnet-4-5",
                      "claude-sonnet-4-20250514", "claude-opus-4-6"]
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.get.return_value = True
            for model_id in supported:
                result = get_effective_context_window(model_id)
                assert result == 1_000_000, f"{model_id} should return 1M"

    def test_returns_200k_for_unsupported_model_even_when_enabled(self):
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.get.return_value = True
            result = get_effective_context_window("claude-3-5-haiku-20241022")
            assert result == 200_000

    def test_returns_200k_for_unknown_model(self):
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.get.return_value = True
            result = get_effective_context_window("nonexistent-model")
            assert result == 200_000

    def test_context_window_used_in_build_kwargs_when_1m_enabled(self):
        """When 1M is enabled, clamping still respects model max_output_tokens."""
        p = AnthropicProvider()
        info = get_model_info("claude-sonnet-4-6")  # max_tokens=64000
        with patch("ai.providers.anthropic_provider.settings") as mock_settings:
            mock_settings.get.return_value = True
            kwargs = p._build_api_kwargs(
                messages=[{"role": "user", "content": "hi"}],
                system="test",
                tools=[],
                max_tokens=100_000,
                model="claude-sonnet-4-6",
                model_info=info,
                use_cache=False,
            )
            # Model's max_output is 64k -- that's the ceiling regardless of context window
            assert kwargs["max_tokens"] == 64_000


# ---------------------------------------------------------------------------
# TASK-150: Streaming happy-path test for AnthropicProvider
# ---------------------------------------------------------------------------

class TestAnthropicStreamHappyPath:
    """TASK-150: Verify streaming produces text deltas on success."""

    @patch("ai.providers.anthropic_provider.ANTHROPIC_AVAILABLE", True)
    @patch("ai.providers.anthropic_provider.anthropic")
    def test_stream_yields_text_deltas(self, mock_anthropic):
        """Mock the Anthropic client's stream context manager
        to yield content_block_delta events and verify callback receives chunks."""
        # Set up provider
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        p = AnthropicProvider()
        p.configure(api_key="sk-ant-test-key")

        # Mock the stream context manager
        mock_final_message = MagicMock()
        mock_final_message.model = "claude-sonnet-4-6"
        mock_final_message.stop_reason = "end_turn"
        mock_final_message.usage.input_tokens = 10
        mock_final_message.usage.output_tokens = 20

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello world"
        mock_final_message.content = [text_block]

        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.text_stream = iter(["Hello", " ", "world"])
        mock_stream.get_final_message.return_value = mock_final_message

        mock_client.messages.stream.return_value = mock_stream

        # Collect text deltas via callback
        received_chunks = []

        def on_text(chunk):
            received_chunks.append(chunk)

        result = p.stream_message(
            messages=[{"role": "user", "content": "hi"}],
            system="You are helpful.",
            tools=[],
            max_tokens=1024,
            model="claude-sonnet-4-6",
            text_callback=on_text,
        )

        assert received_chunks == ["Hello", " ", "world"]
        assert result.model == "claude-sonnet-4-6"
        assert result.stop_reason == "end_turn"
        assert len(result.content) == 1
        assert result.content[0]["text"] == "Hello world"


# ---------------------------------------------------------------------------
# OllamaProvider -- Thinking Model Detection
# ---------------------------------------------------------------------------

class TestOllamaThinkingModel:
    """Test Qwen 3.x thinking model detection and think parameter."""

    def test_is_thinking_model_qwen3(self):
        assert OllamaProvider._is_thinking_model("qwen3:8b") is True
        assert OllamaProvider._is_thinking_model("qwen3:32b") is True
        assert OllamaProvider._is_thinking_model("Qwen3:latest") is True

    def test_is_thinking_model_non_qwen3(self):
        assert OllamaProvider._is_thinking_model("llama3.1") is False
        assert OllamaProvider._is_thinking_model("qwen2.5:7b") is False
        assert OllamaProvider._is_thinking_model("deepseek-r1:32b") is False

    @patch("ai.providers.ollama_provider.requests.post")
    def test_think_param_sent_for_qwen3(self, mock_post):
        """Qwen 3.x models should have think=True in the payload."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "model": "qwen3:8b",
            "message": {"role": "assistant", "content": "Hello!"},
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 3,
        }
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        p.create_message(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=100,
            model="qwen3:8b",
        )

        payload = mock_post.call_args[1]["json"]
        assert payload["think"] is True

    @patch("ai.providers.ollama_provider.requests.post")
    def test_think_param_not_sent_for_non_qwen3(self, mock_post):
        """Non-Qwen3 models should not have think in the payload."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "model": "llama3.1",
            "message": {"role": "assistant", "content": "Hello!"},
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 3,
        }
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        p.create_message(
            messages=[{"role": "user", "content": "hi"}],
            system="test",
            tools=[],
            max_tokens=100,
            model="llama3.1",
        )

        payload = mock_post.call_args[1]["json"]
        assert "think" not in payload


# ---------------------------------------------------------------------------
# OllamaProvider -- Native Streaming
# ---------------------------------------------------------------------------

class TestOllamaNativeStreaming:
    """Test native /api/chat streaming format parsing."""

    @patch("ai.providers.ollama_provider.requests.post")
    def test_stream_text_content(self, mock_post):
        """Streaming text content should be accumulated and callback invoked."""
        chunks = [
            b'{"model":"llama3.1","message":{"role":"assistant","content":"Hello"},"done":false}',
            b'{"model":"llama3.1","message":{"role":"assistant","content":" world"},"done":false}',
            b'{"model":"llama3.1","message":{"role":"assistant","content":""},"done":true,"prompt_eval_count":10,"eval_count":5}',
        ]
        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = iter(chunks)
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        received = []
        p = OllamaProvider()
        result = p.stream_message(
            messages=[{"role": "user", "content": "hi"}],
            system="",
            tools=[],
            max_tokens=100,
            model="llama3.1",
            text_callback=lambda t: received.append(t),
        )

        assert received == ["Hello", " world"]
        assert result.content[0]["text"] == "Hello world"
        assert result.stop_reason == "end_turn"
        assert result.usage["input_tokens"] == 10
        assert result.usage["output_tokens"] == 5

    @patch("ai.providers.ollama_provider.requests.post")
    def test_stream_tool_calls(self, mock_post):
        """Tool calls in streaming should be collected correctly."""
        chunks = [
            b'{"model":"llama3.1","message":{"role":"assistant","tool_calls":[{"function":{"name":"get_info","arguments":{"key":"val"}}}]},"done":false}',
            b'{"model":"llama3.1","message":{"role":"assistant","content":""},"done":true,"prompt_eval_count":20,"eval_count":10}',
        ]
        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = iter(chunks)
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        result = p.stream_message(
            messages=[{"role": "user", "content": "do something"}],
            system="",
            tools=[{"name": "get_info", "description": "Get info", "input_schema": {}}],
            max_tokens=100,
            model="llama3.1",
        )

        assert result.stop_reason == "tool_use"
        tool_blocks = [b for b in result.content if b["type"] == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0]["name"] == "get_info"
        assert tool_blocks[0]["input"] == {"key": "val"}
        assert tool_blocks[0]["id"].startswith("toolu_")

    @patch("ai.providers.ollama_provider.requests.post")
    def test_stream_thinking_content(self, mock_post):
        """Thinking content from native streaming should be collected as reasoning."""
        chunks = [
            b'{"model":"qwen3:8b","message":{"role":"assistant","thinking":"Let me think..."},"done":false}',
            b'{"model":"qwen3:8b","message":{"role":"assistant","content":"The answer is 42."},"done":false}',
            b'{"model":"qwen3:8b","message":{"role":"assistant","content":""},"done":true,"prompt_eval_count":15,"eval_count":8}',
        ]
        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = iter(chunks)
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        result = p.stream_message(
            messages=[{"role": "user", "content": "what is the meaning of life"}],
            system="",
            tools=[],
            max_tokens=100,
            model="qwen3:8b",
        )

        assert result.reasoning == "Let me think..."
        assert result.content[0]["text"] == "The answer is 42."

    @patch("ai.providers.ollama_provider.requests.post")
    def test_stream_endpoint_is_native(self, mock_post):
        """Streaming should use /api/chat, not /v1/chat/completions."""
        chunks = [
            b'{"model":"llama3.1","message":{"role":"assistant","content":"ok"},"done":true,"prompt_eval_count":1,"eval_count":1}',
        ]
        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = iter(chunks)
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        p = OllamaProvider()
        p.stream_message(
            messages=[{"role": "user", "content": "hi"}],
            system="",
            tools=[],
            max_tokens=100,
            model="llama3.1",
        )

        call_args = mock_post.call_args
        assert "/api/chat" in call_args[0][0]
        assert "/v1/" not in call_args[0][0]

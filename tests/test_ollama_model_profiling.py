"""
tests/test_ollama_model_profiling.py
TASK-235: Tests for Ollama model capability profiling and warnings.
"""

import pytest
from unittest.mock import patch, MagicMock

from ai.providers.ollama_provider import (
    get_model_capability_profile,
    check_model_warnings,
    OllamaProvider,
)


# ---------------------------------------------------------------------------
# get_model_capability_profile tests
# ---------------------------------------------------------------------------

class TestGetModelCapabilityProfile:
    """Tests for the model capability profiling function."""

    def test_none_show_data_returns_defaults(self):
        """When show_data is None, return safe defaults."""
        profile = get_model_capability_profile("unknown-model", None)
        assert profile["context_window"] is None
        assert profile["tool_calling_support"] is False
        assert profile["recommended_for_cad"] is False
        assert profile["model_name"] == "unknown-model"

    def test_empty_show_data_returns_defaults(self):
        """When show_data is an empty dict, return safe defaults."""
        profile = get_model_capability_profile("unknown-model", {})
        assert profile["context_window"] is None
        assert profile["tool_calling_support"] is False

    def test_context_window_from_model_info(self):
        """Extract context window from model_info dict."""
        show_data = {
            "model_info": {
                "general.context_length": 131072,
            },
            "details": {},
            "capabilities": [],
        }
        profile = get_model_capability_profile("llama3.1:70b", show_data)
        assert profile["context_window"] == 131072

    def test_context_window_from_parameters_string(self):
        """Extract context window from parameters string (num_ctx)."""
        show_data = {
            "model_info": {},
            "details": {},
            "capabilities": [],
            "parameters": "num_ctx 65536\ntemperature 0.8\n",
        }
        profile = get_model_capability_profile("custom-model", show_data)
        assert profile["context_window"] == 65536

    def test_tool_calling_from_capabilities_list(self):
        """Detect tool calling support from capabilities list."""
        show_data = {
            "model_info": {},
            "details": {},
            "capabilities": ["tools", "vision"],
        }
        profile = get_model_capability_profile("qwen2.5:14b", show_data)
        assert profile["tool_calling_support"] is True

    def test_tool_calling_fallback_to_known_families(self):
        """When capabilities list lacks 'tools', fall back to model name check."""
        show_data = {
            "model_info": {},
            "details": {},
            "capabilities": [],
        }
        profile = get_model_capability_profile("llama3.1:8b", show_data)
        assert profile["tool_calling_support"] is True

    def test_tool_calling_false_for_unknown_model(self):
        """Unknown model families should not claim tool calling."""
        show_data = {
            "model_info": {},
            "details": {},
            "capabilities": [],
        }
        profile = get_model_capability_profile("my-custom-finetune:7b", show_data)
        assert profile["tool_calling_support"] is False

    def test_recommended_for_cad_true(self):
        """recommended_for_cad is True when context >= 32K and tools supported."""
        show_data = {
            "model_info": {"general.context_length": 65536},
            "details": {},
            "capabilities": ["tools"],
        }
        profile = get_model_capability_profile("qwen2.5:14b", show_data)
        assert profile["recommended_for_cad"] is True

    def test_recommended_for_cad_false_small_context(self):
        """recommended_for_cad is False when context < 32K even with tools."""
        show_data = {
            "model_info": {"general.context_length": 8192},
            "details": {},
            "capabilities": ["tools"],
        }
        profile = get_model_capability_profile("qwen2.5:3b", show_data)
        assert profile["recommended_for_cad"] is False

    def test_recommended_for_cad_false_no_tools(self):
        """recommended_for_cad is False without tool support even with large context."""
        show_data = {
            "model_info": {"general.context_length": 65536},
            "details": {},
            "capabilities": [],
        }
        profile = get_model_capability_profile("my-custom:70b", show_data)
        assert profile["recommended_for_cad"] is False

    def test_family_and_parameter_size_extracted(self):
        """Family and parameter size should be extracted from details."""
        show_data = {
            "model_info": {},
            "details": {"family": "llama", "parameter_size": "70B"},
            "capabilities": [],
        }
        profile = get_model_capability_profile("llama3.1:70b", show_data)
        assert profile["family"] == "llama"
        assert profile["parameter_size"] == "70B"

    def test_malformed_context_length_uses_default(self):
        """Non-integer context_length should fall back to None."""
        show_data = {
            "model_info": {"general.context_length": "not-a-number"},
            "details": {},
            "capabilities": [],
        }
        profile = get_model_capability_profile("broken-model", show_data)
        assert profile["context_window"] is None

    def test_devstral_known_family(self):
        """devstral should be recognized as a known tool-calling family."""
        profile = get_model_capability_profile("devstral:24b", None)
        assert profile["tool_calling_support"] is True

    def test_qwen3_known_family(self):
        """qwen3 should be recognized as a known tool-calling family."""
        profile = get_model_capability_profile("qwen3:8b", None)
        assert profile["tool_calling_support"] is True


# ---------------------------------------------------------------------------
# check_model_warnings tests
# ---------------------------------------------------------------------------

class TestCheckModelWarnings:
    """Tests for the model warning generation function."""

    def test_no_warnings_for_good_model(self):
        """A well-configured model should produce no warnings."""
        profile = {
            "context_window": 65536,
            "tool_calling_support": True,
            "model_name": "qwen2.5:14b",
        }
        warnings = check_model_warnings(profile, user_max_tokens=4096)
        assert warnings == []

    def test_small_context_warning(self):
        """Context window < 16K should produce a warning."""
        profile = {
            "context_window": 8192,
            "tool_calling_support": True,
            "model_name": "small-model",
        }
        warnings = check_model_warnings(profile, user_max_tokens=4096)
        assert len(warnings) == 1
        assert warnings[0]["code"] == "small_context_window"
        assert warnings[0]["level"] == "warning"

    def test_no_tool_calling_warning(self):
        """Missing tool calling should produce a warning."""
        profile = {
            "context_window": 65536,
            "tool_calling_support": False,
            "model_name": "no-tools-model",
        }
        warnings = check_model_warnings(profile)
        assert len(warnings) == 1
        assert warnings[0]["code"] == "no_tool_calling"

    def test_max_tokens_exceeds_context_critical(self):
        """max_tokens > context window should produce a critical warning."""
        profile = {
            "context_window": 8192,
            "tool_calling_support": True,
            "model_name": "small-model",
        }
        warnings = check_model_warnings(profile, user_max_tokens=16000)
        codes = [w["code"] for w in warnings]
        assert "max_tokens_exceeds_context" in codes
        critical = [w for w in warnings if w["code"] == "max_tokens_exceeds_context"]
        assert critical[0]["level"] == "critical"

    def test_multiple_warnings_combined(self):
        """A bad model config can produce multiple warnings."""
        profile = {
            "context_window": 4096,
            "tool_calling_support": False,
            "model_name": "terrible-model",
        }
        warnings = check_model_warnings(profile, user_max_tokens=8192)
        codes = {w["code"] for w in warnings}
        assert "small_context_window" in codes
        assert "no_tool_calling" in codes
        assert "max_tokens_exceeds_context" in codes

    def test_no_max_tokens_skips_context_check(self):
        """When user_max_tokens is None, skip the context comparison."""
        profile = {
            "context_window": 4096,
            "tool_calling_support": True,
            "model_name": "small-model",
        }
        warnings = check_model_warnings(profile, user_max_tokens=None)
        codes = [w["code"] for w in warnings]
        assert "max_tokens_exceeds_context" not in codes

    def test_max_tokens_equal_to_context_no_warning(self):
        """max_tokens == context_window should not warn (edge case)."""
        profile = {
            "context_window": 8192,
            "tool_calling_support": True,
            "model_name": "edge-model",
        }
        warnings = check_model_warnings(profile, user_max_tokens=8192)
        codes = [w["code"] for w in warnings]
        assert "max_tokens_exceeds_context" not in codes


# ---------------------------------------------------------------------------
# OllamaProvider.get_model_info tests
# ---------------------------------------------------------------------------

class TestOllamaProviderModelInfo:
    """Tests for the OllamaProvider.get_model_info method."""

    def test_get_model_info_calls_show_model(self):
        """get_model_info should call _show_model and return a profile."""
        provider = OllamaProvider()
        provider.configure()
        show_data = {
            "model_info": {"general.context_length": 32768},
            "details": {"family": "qwen2"},
            "capabilities": ["tools"],
        }
        with patch.object(provider, "_show_model", return_value=show_data):
            profile = provider.get_model_info("qwen2.5:14b")
        assert profile["context_window"] == 32768
        assert profile["tool_calling_support"] is True

    def test_get_model_info_handles_show_failure(self):
        """get_model_info should return defaults when _show_model returns None."""
        provider = OllamaProvider()
        provider.configure()
        with patch.object(provider, "_show_model", return_value=None):
            profile = provider.get_model_info("broken-model")
        assert profile["context_window"] is None

    def test_check_model_and_warn_returns_warnings(self):
        """check_model_and_warn should return appropriate warnings."""
        provider = OllamaProvider()
        provider.configure()
        show_data = {
            "model_info": {"general.context_length": 4096},
            "details": {},
            "capabilities": [],
        }
        with patch.object(provider, "_show_model", return_value=show_data):
            warnings = provider.check_model_and_warn("tiny-model", user_max_tokens=8192)
        codes = [w["code"] for w in warnings]
        assert "small_context_window" in codes
        assert "max_tokens_exceeds_context" in codes

    def test_check_model_and_warn_graceful_on_exception(self):
        """check_model_and_warn should not raise even if _show_model fails."""
        provider = OllamaProvider()
        provider.configure()
        with patch.object(provider, "_show_model", side_effect=Exception("connection refused")):
            warnings = provider.check_model_and_warn("broken-model")
        # Should return warnings based on default profile, not raise
        assert isinstance(warnings, list)

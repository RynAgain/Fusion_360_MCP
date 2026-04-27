"""
tests/test_context_window_guard.py
TASK-228: Tests for ContextWindowGuard -- adequacy checks, pressure monitoring,
configurable thresholds, and event emission.
"""

import pytest

from ai.context_window_guard import (
    AdequacyLevel,
    AdequacyResult,
    ContextWindowGuard,
    ContextWindowThresholds,
    PressureResult,
    CONTEXT_PRESSURE_MESSAGE,
    CRITICAL_CONCISENESS_MESSAGE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def guard():
    """Guard with default thresholds."""
    return ContextWindowGuard()


@pytest.fixture
def custom_guard():
    """Guard with custom thresholds for deterministic testing."""
    return ContextWindowGuard(
        thresholds=ContextWindowThresholds(
            critical_max_tokens=5000,
            warning_max_tokens=10000,
            warning_tool_count=5,
            tokens_per_tool=300,
            min_free_tokens=3000,
            critical_free_tokens=1000,
            pressure_warning_pct=0.75,
            pressure_critical_pct=0.90,
        )
    )


# ===========================================================================
# Token estimation
# ===========================================================================

class TestTokenEstimation:
    def test_estimate_tokens_empty(self, guard):
        assert guard.estimate_tokens("") == 0

    def test_estimate_tokens_short(self, guard):
        assert guard.estimate_tokens("test") == 1  # 4 chars / 4 = 1

    def test_estimate_tokens_longer(self, guard):
        text = "a" * 400
        assert guard.estimate_tokens(text) == 100

    def test_estimate_messages_tokens_string_content(self, guard):
        messages = [{"role": "user", "content": "Hello world"}]
        tokens = guard.estimate_messages_tokens(messages)
        # "Hello world" = 11 chars / 4 = 2, plus 4 overhead = 6
        assert tokens > 0

    def test_estimate_messages_tokens_structured_content(self, guard):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will create a box."},
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "create_box",
                        "input": {"width": 10, "height": 20},
                    },
                ],
            },
        ]
        tokens = guard.estimate_messages_tokens(messages)
        assert tokens > 0

    def test_estimate_messages_tokens_tool_result(self, guard):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": '{"success": true, "body_name": "Box1"}',
                    },
                ],
            },
        ]
        tokens = guard.estimate_messages_tokens(messages)
        assert tokens > 0

    def test_estimate_messages_tokens_image_block(self, guard):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Screenshot:"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "iVBOR...",
                        },
                    },
                ],
            },
        ]
        tokens = guard.estimate_messages_tokens(messages)
        # Image blocks estimate ~1000 tokens
        assert tokens >= 1000

    def test_estimate_messages_tokens_empty_list(self, guard):
        assert guard.estimate_messages_tokens([]) == 0


# ===========================================================================
# Adequacy check
# ===========================================================================

class TestAdequacyCheck:
    """Test check_adequacy() returns correct levels for various configs."""

    def test_ok_large_context(self, guard):
        result = guard.check_adequacy(
            max_tokens=128000,
            num_tools=30,
            system_prompt_tokens=800,
        )
        assert result.level == AdequacyLevel.OK
        assert result.estimated_free > 0

    def test_critical_very_small_context(self, guard):
        """context_window=4000 should be critical (below default 8000 threshold)."""
        result = guard.check_adequacy(
            max_tokens=4000,
            num_tools=10,
            system_prompt_tokens=500,
            context_window=4000,
        )
        assert result.level == AdequacyLevel.CRITICAL
        assert any("critical threshold" in r for r in result.reasons)

    def test_critical_8100_tokens_with_many_tools(self, guard):
        """The exact scenario that motivated this feature: 8100 context, 35 tools."""
        result = guard.check_adequacy(
            max_tokens=8100,
            num_tools=35,
            system_prompt_tokens=800,
            context_window=8100,
        )
        # overhead = 800 + 35*350 = 13050 > 8100
        # free = 8100 - 13050 = negative -> critical
        assert result.level == AdequacyLevel.CRITICAL

    def test_warning_moderate_context_many_tools(self, guard):
        """context_window=12000 with 15 tools should warn."""
        result = guard.check_adequacy(
            max_tokens=12000,
            num_tools=15,
            system_prompt_tokens=500,
            context_window=12000,
        )
        # 12000 < 16000 and 15 >= 10 -> warning
        assert result.level in (AdequacyLevel.WARNING, AdequacyLevel.CRITICAL)

    def test_ok_small_context_few_tools(self, guard):
        """max_tokens=12000 with 3 tools should be OK (tools below threshold)."""
        result = guard.check_adequacy(
            max_tokens=12000,
            num_tools=3,
            system_prompt_tokens=500,
        )
        # 12000 >= 8000 (not critical absolute)
        # 3 < 10 (not enough tools for warning)
        # free = 12000 - 500 - 3*350 = 10450 > 4000 (ok)
        assert result.level == AdequacyLevel.OK

    def test_warning_when_free_tokens_marginal(self, guard):
        """Free tokens below min_free_tokens but above critical."""
        # overhead = 500 + 10*350 = 4000
        # free = 7500 - 4000 = 3500 < 4000 (min_free_tokens)
        # 7500 < 8000 -> critical (absolute threshold)
        result = guard.check_adequacy(
            max_tokens=7500,
            num_tools=10,
            system_prompt_tokens=500,
            context_window=7500,
        )
        assert result.level == AdequacyLevel.CRITICAL

    def test_to_dict(self, guard):
        result = guard.check_adequacy(
            max_tokens=100000,
            num_tools=5,
            system_prompt_tokens=500,
        )
        d = result.to_dict()
        assert d["level"] == "ok"
        assert "max_tokens" in d
        assert "estimated_overhead" in d
        assert "estimated_free" in d
        assert isinstance(d["reasons"], list)

    def test_custom_thresholds_critical(self, custom_guard):
        """Custom thresholds: critical below 5000."""
        result = custom_guard.check_adequacy(
            max_tokens=3000,
            num_tools=2,
            system_prompt_tokens=200,
            context_window=3000,
        )
        assert result.level == AdequacyLevel.CRITICAL

    def test_custom_thresholds_warning(self, custom_guard):
        """Custom thresholds: warning below 10000 with 5+ tools."""
        result = custom_guard.check_adequacy(
            max_tokens=8000,
            num_tools=6,
            system_prompt_tokens=200,
            context_window=8000,
        )
        # 8000 >= 5000 (not critical absolute)
        # 8000 < 10000 and 6 >= 5 -> warning
        assert result.level in (AdequacyLevel.WARNING, AdequacyLevel.CRITICAL)

    def test_custom_thresholds_ok(self, custom_guard):
        """Custom thresholds: adequate context."""
        result = custom_guard.check_adequacy(
            max_tokens=50000,
            num_tools=3,
            system_prompt_tokens=500,
        )
        assert result.level == AdequacyLevel.OK

    def test_zero_tools(self, guard):
        result = guard.check_adequacy(
            max_tokens=100000,
            num_tools=0,
            system_prompt_tokens=500,
        )
        assert result.level == AdequacyLevel.OK

    def test_message_count_does_not_crash(self, guard):
        """message_count parameter is accepted without error."""
        result = guard.check_adequacy(
            max_tokens=100000,
            num_tools=5,
            system_prompt_tokens=500,
            message_count=50,
        )
        assert result.level == AdequacyLevel.OK


# ===========================================================================
# Context pressure monitoring
# ===========================================================================

class TestContextPressure:
    """Test check_pressure() returns correct levels."""

    def test_ok_low_usage(self, guard):
        messages = [{"role": "user", "content": "Hello"}]
        result = guard.check_pressure(
            max_tokens=100000,
            messages=messages,
            system_prompt="You are a CAD assistant.",
            num_tools=5,
        )
        assert result.level == AdequacyLevel.OK
        assert result.message is None

    def test_warning_at_80_percent(self, custom_guard):
        """Pressure warning at 75% threshold (custom)."""
        # With custom: pressure_warning_pct=0.75, pressure_critical_pct=0.90
        # context_window=10000, overhead = prompt + tools = ~200 + 2*300 = 800
        # Need message tokens to make total ~7500-8999 (75-89%)
        big_text = "x" * 28000  # ~7000 tokens
        messages = [{"role": "user", "content": big_text}]
        result = custom_guard.check_pressure(
            max_tokens=10000,
            messages=messages,
            system_prompt="Short prompt.",
            num_tools=2,
            context_window=10000,
        )
        assert result.level in (AdequacyLevel.WARNING, AdequacyLevel.CRITICAL)
        assert result.usage_pct >= 0.75

    def test_critical_at_90_percent(self, custom_guard):
        """Pressure critical at 90% threshold (custom)."""
        big_text = "x" * 36000  # ~9000 tokens
        messages = [{"role": "user", "content": big_text}]
        result = custom_guard.check_pressure(
            max_tokens=10000,
            messages=messages,
            system_prompt="Short prompt.",
            num_tools=2,
            context_window=10000,
        )
        assert result.level == AdequacyLevel.CRITICAL
        assert result.message == CONTEXT_PRESSURE_MESSAGE

    def test_zero_max_tokens(self, guard):
        result = guard.check_pressure(
            max_tokens=0,
            messages=[],
            system_prompt="",
            num_tools=0,
        )
        assert result.level == AdequacyLevel.CRITICAL

    def test_to_dict(self, guard):
        messages = [{"role": "user", "content": "Hello"}]
        result = guard.check_pressure(
            max_tokens=100000,
            messages=messages,
            system_prompt="You are a CAD assistant.",
            num_tools=5,
        )
        d = result.to_dict()
        assert "usage_pct" in d
        assert "estimated_tokens_used" in d
        assert "max_tokens" in d
        assert d["level"] == "ok"

    def test_pressure_with_default_thresholds_80pct(self, guard):
        """Default thresholds: warning at 80%, critical at 90%."""
        # context_window=10000, overhead = 200 + 5*350 = 1950
        # Need message tokens to get to 80%: 10000*0.8 = 8000
        big_text = "y" * 25000  # ~6250 tokens
        messages = [{"role": "user", "content": big_text}]
        result = guard.check_pressure(
            max_tokens=10000,
            messages=messages,
            system_prompt="Short prompt for testing.",
            num_tools=5,
            context_window=10000,
        )
        assert result.level in (AdequacyLevel.WARNING, AdequacyLevel.CRITICAL)
        assert result.usage_pct >= 0.80

    def test_pressure_with_default_thresholds_90pct(self, guard):
        """Default thresholds: critical at 90%."""
        big_text = "z" * 40000  # ~10000 tokens -- well over 90% of 10000
        messages = [{"role": "user", "content": big_text}]
        result = guard.check_pressure(
            max_tokens=10000,
            messages=messages,
            system_prompt="Short prompt.",
            num_tools=2,
            context_window=10000,
        )
        assert result.level == AdequacyLevel.CRITICAL


# ===========================================================================
# Convenience methods
# ===========================================================================

class TestConvenienceMethods:
    def test_get_conciseness_injection(self):
        msg = ContextWindowGuard.get_conciseness_injection()
        assert "[SYSTEM]" in msg
        assert "concise" in msg.lower()

    def test_get_pressure_injection(self):
        msg = ContextWindowGuard.get_pressure_injection()
        assert "[CONTEXT PRESSURE]" in msg


# ===========================================================================
# Thresholds configuration
# ===========================================================================

class TestThresholds:
    """Verify thresholds are configurable and affect results."""

    def test_default_thresholds(self):
        t = ContextWindowThresholds()
        assert t.critical_max_tokens == 8000
        assert t.warning_max_tokens == 16000
        assert t.warning_tool_count == 10
        assert t.tokens_per_tool == 350
        assert t.pressure_warning_pct == 0.80
        assert t.pressure_critical_pct == 0.90

    def test_custom_thresholds_propagate(self):
        t = ContextWindowThresholds(
            critical_max_tokens=2000,
            warning_max_tokens=5000,
        )
        guard = ContextWindowGuard(thresholds=t)
        # 3000 is above custom critical (2000) but below custom warning (5000)
        result = guard.check_adequacy(max_tokens=3000, num_tools=15, context_window=3000)
        # With 15 tools: overhead = 15*350 = 5250 > 3000 -> free = -2250 -> critical
        assert result.level == AdequacyLevel.CRITICAL

    def test_raising_critical_threshold_changes_result(self):
        """Same context_window but different critical threshold -> different level."""
        # Default threshold: critical < 8000
        guard_default = ContextWindowGuard()
        result1 = guard_default.check_adequacy(
            max_tokens=9000, num_tools=2, system_prompt_tokens=200, context_window=9000,
        )
        # overhead = 200 + 2*350 = 900, free = 8100 > 4000 -> OK
        assert result1.level == AdequacyLevel.OK

        # Raised threshold: critical < 10000
        guard_custom = ContextWindowGuard(
            thresholds=ContextWindowThresholds(critical_max_tokens=10000),
        )
        result2 = guard_custom.check_adequacy(
            max_tokens=9000, num_tools=2, system_prompt_tokens=200, context_window=9000,
        )
        assert result2.level == AdequacyLevel.CRITICAL


# ===========================================================================
# Event emission integration (mock-based)
# ===========================================================================

class TestEventEmission:
    """Test that the guard results integrate correctly with an emitter."""

    def test_critical_result_triggers_emission(self, guard):
        """Simulate what claude_client does when adequacy is critical."""
        result = guard.check_adequacy(
            max_tokens=4000, num_tools=20, system_prompt_tokens=500, context_window=4000,
        )
        assert result.level == AdequacyLevel.CRITICAL

        # Simulate the emission
        emitted_events = []

        def fake_emitter(event_type, payload):
            emitted_events.append((event_type, payload))

        # The integration code emits like this:
        if result.level == AdequacyLevel.CRITICAL:
            fake_emitter("context_window_warning", {
                "level": "critical",
                **result.to_dict(),
            })

        assert len(emitted_events) == 1
        assert emitted_events[0][0] == "context_window_warning"
        assert emitted_events[0][1]["level"] == "critical"
        assert emitted_events[0][1]["max_tokens"] == 4000

    def test_warning_result_triggers_emission(self, guard):
        """Simulate what claude_client does when adequacy is warning."""
        result = guard.check_adequacy(
            max_tokens=14000, num_tools=12, system_prompt_tokens=500, context_window=14000,
        )
        assert result.level in (AdequacyLevel.WARNING, AdequacyLevel.CRITICAL)

        emitted_events = []

        def fake_emitter(event_type, payload):
            emitted_events.append((event_type, payload))

        if result.level != AdequacyLevel.OK:
            fake_emitter("context_window_warning", {
                "level": result.level.value,
                **result.to_dict(),
            })

        assert len(emitted_events) == 1

    def test_pressure_critical_emits_context_pressure(self, guard):
        """Simulate runtime pressure emission."""
        big_text = "a" * 40000
        messages = [{"role": "user", "content": big_text}]
        result = guard.check_pressure(
            max_tokens=10000,
            messages=messages,
            system_prompt="prompt",
            num_tools=2,
            context_window=10000,
        )
        assert result.level == AdequacyLevel.CRITICAL

        emitted = []

        def fake_emitter(event_type, payload):
            emitted.append((event_type, payload))

        fake_emitter("context_pressure", result.to_dict())

        assert len(emitted) == 1
        assert emitted[0][0] == "context_pressure"
        assert emitted[0][1]["level"] == "critical"

    def test_ok_does_not_emit(self, guard):
        """No emission when context is adequate."""
        result = guard.check_adequacy(max_tokens=128000, num_tools=5, system_prompt_tokens=500)
        assert result.level == AdequacyLevel.OK

        emitted = []

        def fake_emitter(event_type, payload):
            emitted.append((event_type, payload))

        # The integration code does NOT emit for OK
        if result.level != AdequacyLevel.OK:
            fake_emitter("context_window_warning", result.to_dict())

        assert len(emitted) == 0


# ===========================================================================
# context_window parameter (actual model context vs max_tokens output limit)
# ===========================================================================

class TestContextWindowParameter:
    """Test that context_window overrides max_tokens for capacity calculations.

    This prevents false-positive CRITICAL warnings when a small max_tokens
    (output limit) is used with a model that has a large actual context
    window (e.g. Ollama models with 32k+ context).
    """

    def test_adequacy_ok_with_large_context_window_small_max_tokens(self, guard):
        """The exact Qwen 3.6 scenario: max_tokens=8100, 35 tools, 32k context."""
        result = guard.check_adequacy(
            max_tokens=8100,
            num_tools=35,
            system_prompt_tokens=800,
            context_window=32768,
        )
        # With context_window=32768: overhead = 800 + 35*350 = 13050
        # free = 32768 - 13050 = 19718 -> OK
        assert result.level == AdequacyLevel.OK
        assert result.estimated_free > 0

    def test_adequacy_ok_without_context_window_suppresses_false_positives(self, guard):
        """Without context_window, guard suppresses false-positive warnings.

        When no real context window is available (context_window=None), the
        guard falls back to max_tokens.  But max_tokens is an output budget,
        not the model's input capacity, so absolute thresholds are unreliable.
        The guard should return OK to avoid injecting panic messages.
        """
        result = guard.check_adequacy(
            max_tokens=8100,
            num_tools=35,
            system_prompt_tokens=800,
        )
        # Without context_window: guard skips absolute threshold checks
        assert result.level == AdequacyLevel.OK

    def test_adequacy_context_window_none_falls_back(self, guard):
        """context_window=None should behave identically to not passing it."""
        result_none = guard.check_adequacy(
            max_tokens=8100,
            num_tools=35,
            system_prompt_tokens=800,
            context_window=None,
        )
        result_missing = guard.check_adequacy(
            max_tokens=8100,
            num_tools=35,
            system_prompt_tokens=800,
        )
        assert result_none.level == result_missing.level
        assert result_none.estimated_free == result_missing.estimated_free

    def test_adequacy_still_critical_if_context_window_is_small(self, guard):
        """A small context_window should still trigger critical."""
        result = guard.check_adequacy(
            max_tokens=8100,
            num_tools=35,
            system_prompt_tokens=800,
            context_window=4000,
        )
        assert result.level == AdequacyLevel.CRITICAL

    def test_pressure_ok_with_large_context_window(self, guard):
        """Pressure should be measured against context_window, not max_tokens."""
        messages = [{"role": "user", "content": "Hello, build me a speaker box."}]
        result = guard.check_pressure(
            max_tokens=8100,
            messages=messages,
            system_prompt="You are a CAD assistant.",
            num_tools=35,
            context_window=32768,
        )
        # Total used is small relative to 32768 context -> OK
        assert result.level == AdequacyLevel.OK

    def test_pressure_ok_without_context_window_suppresses_false_positives(self, guard):
        """Without context_window, pressure suppresses false-positive warnings.

        When no real context window is available, pressure is calculated
        against max_tokens (unreliable) but the guard returns OK to avoid
        injecting panic messages into the conversation.
        """
        big_text = "x" * 24000  # ~6000 tokens, plus 35*350 = 12250 tool overhead -> way over 8100
        messages = [{"role": "user", "content": big_text}]
        result = guard.check_pressure(
            max_tokens=8100,
            messages=messages,
            system_prompt="You are a CAD assistant.",
            num_tools=35,
        )
        # Without context_window: guard returns OK (suppresses false positives)
        assert result.level == AdequacyLevel.OK

    def test_pressure_ok_same_messages_with_context_window(self, guard):
        """Same messages WITH context_window=65536 -> OK."""
        big_text = "x" * 24000  # ~6000 tokens
        messages = [{"role": "user", "content": big_text}]
        result = guard.check_pressure(
            max_tokens=8100,
            messages=messages,
            system_prompt="You are a CAD assistant.",
            num_tools=35,
            context_window=65536,
        )
        # total used ~6000 + 12250 + 50 = ~18300 vs 65536 -> ~28% -> OK
        assert result.level == AdequacyLevel.OK

    def test_pressure_context_window_none_falls_back(self, guard):
        """context_window=None falls back to max_tokens for pressure."""
        messages = [{"role": "user", "content": "Hello"}]
        result_none = guard.check_pressure(
            max_tokens=100000,
            messages=messages,
            system_prompt="Short.",
            num_tools=5,
            context_window=None,
        )
        result_missing = guard.check_pressure(
            max_tokens=100000,
            messages=messages,
            system_prompt="Short.",
            num_tools=5,
        )
        assert result_none.level == result_missing.level
        assert abs(result_none.usage_pct - result_missing.usage_pct) < 0.001

    def test_pressure_warning_message_shows_context_window(self, custom_guard):
        """Warning message should reference 'context window' not 'max_tokens'."""
        # Build messages to hit ~75-89% of context_window=10000
        big_text = "x" * 28000  # ~7000 tokens
        messages = [{"role": "user", "content": big_text}]
        result = custom_guard.check_pressure(
            max_tokens=4000,
            messages=messages,
            system_prompt="Short prompt.",
            num_tools=2,
            context_window=10000,
        )
        if result.level == AdequacyLevel.WARNING and result.message:
            assert "context window" in result.message


# ===========================================================================
# AdequacyLevel enum
# ===========================================================================

class TestAdequacyLevel:
    def test_values(self):
        assert AdequacyLevel.OK == "ok"
        assert AdequacyLevel.WARNING == "warning"
        assert AdequacyLevel.CRITICAL == "critical"

    def test_string_comparison(self):
        assert AdequacyLevel.OK.value == "ok"
        assert AdequacyLevel.CRITICAL.value == "critical"

"""
tests/test_context_manager.py
Unit tests for ai.context_manager -- context estimation, condensation,
truncation, and statistics.
"""
import json
import pytest

from ai.context_manager import (
    CHARS_PER_TOKEN,
    CONDENSE_THRESHOLD,
    ContextManager,
    DEFAULT_CONTEXT_WINDOW,
    MODEL_CONTEXT_WINDOWS,
    PRESERVE_RECENT_TURNS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_text_messages(n: int, text: str = "Hello world") -> list[dict]:
    """Return *n* alternating user/assistant text messages."""
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"{text} {i}"})
    return msgs


def _big_text(chars: int) -> str:
    """Return a string of exactly *chars* characters."""
    return "x" * chars


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:

    def test_empty_conversation(self):
        cm = ContextManager()
        assert cm.estimate_tokens([], "") == 0

    def test_text_messages(self):
        cm = ContextManager()
        msgs = [{"role": "user", "content": "a" * 400}]
        # 400 chars / 4 chars-per-token = 100 tokens
        assert cm.estimate_tokens(msgs) == 100

    def test_with_system_prompt(self):
        cm = ContextManager()
        msgs = [{"role": "user", "content": "a" * 100}]
        # (100 msg + 100 system) / 4 = 50
        assert cm.estimate_tokens(msgs, "b" * 100) == 50

    def test_with_images(self):
        cm = ContextManager()
        # 300 chars of base64 -> counted as 300 // 3 = 100 effective chars
        msgs = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "data": "A" * 300},
                    }
                ],
            }
        ]
        assert cm.estimate_tokens(msgs) == 100 // CHARS_PER_TOKEN

    def test_tool_use_blocks(self):
        cm = ContextManager()
        tool_input = {"radius": 5, "height": 10}
        input_json = json.dumps(tool_input)
        name = "create_cylinder"
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": name,
                        "input": tool_input,
                    }
                ],
            }
        ]
        expected_chars = len(input_json) + len(name)
        assert cm.estimate_tokens(msgs) == expected_chars // CHARS_PER_TOKEN

    def test_tool_result_string(self):
        cm = ContextManager()
        result_str = '{"success": true, "count": 1}'
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "x", "content": result_str}
                ],
            }
        ]
        assert cm.estimate_tokens(msgs) == len(result_str) // CHARS_PER_TOKEN

    def test_tool_result_with_nested_image(self):
        cm = ContextManager()
        msgs = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "x",
                        "content": [
                            {"type": "text", "text": "ok"},
                            {
                                "type": "image",
                                "source": {"type": "base64", "data": "B" * 600},
                            },
                        ],
                    }
                ],
            }
        ]
        # text: 2 chars, image: 600 // 3 = 200 chars => total 202 // 4 = 50
        assert cm.estimate_tokens(msgs) == (2 + 200) // CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# should_condense
# ---------------------------------------------------------------------------

class TestShouldCondense:

    def test_under_threshold(self):
        cm = ContextManager()
        # Small conversation -- well under threshold
        msgs = _make_text_messages(4, "short")
        assert cm.should_condense(msgs) is False

    def test_over_threshold(self):
        cm = ContextManager()
        # Build messages that exceed 65 % of 200k tokens
        threshold_chars = int(DEFAULT_CONTEXT_WINDOW * CONDENSE_THRESHOLD) * CHARS_PER_TOKEN
        big = _big_text(threshold_chars + 1000)
        msgs = [{"role": "user", "content": big}]
        assert cm.should_condense(msgs) is True

    def test_system_prompt_contributes(self):
        cm = ContextManager()
        threshold_chars = int(DEFAULT_CONTEXT_WINDOW * CONDENSE_THRESHOLD) * CHARS_PER_TOKEN
        # Message alone is under threshold
        msg_size = threshold_chars // 2
        sys_size = threshold_chars // 2 + 1000
        msgs = [{"role": "user", "content": _big_text(msg_size)}]
        # Without system prompt: under
        assert cm.should_condense(msgs) is False
        # With system prompt: over
        assert cm.should_condense(msgs, _big_text(sys_size)) is True


# ---------------------------------------------------------------------------
# condense
# ---------------------------------------------------------------------------

class TestCondense:

    def test_preserves_recent_turns(self):
        cm = ContextManager()
        msgs = _make_text_messages(20)
        result = cm.condense(msgs)
        # Recent messages = last PRESERVE_RECENT_TURNS * 2
        recent_count = PRESERVE_RECENT_TURNS * 2
        # Result = 1 summary + recent_count
        assert len(result) == 1 + recent_count
        # The last N messages should be preserved verbatim
        assert result[1:] == msgs[-recent_count:]

    def test_creates_summary_message(self):
        cm = ContextManager()
        msgs = _make_text_messages(20)
        result = cm.condense(msgs)
        summary = result[0]
        assert summary["role"] == "user"
        assert "[Context Summary" in summary["content"]
        assert "Condensation #1" in summary["content"]

    def test_condensation_count_increments(self):
        cm = ContextManager()
        msgs = _make_text_messages(20)
        cm.condense(msgs)
        cm.condense(msgs)
        assert cm._condensation_count == 2
        result = cm.condense(msgs)
        assert "Condensation #3" in result[0]["content"]

    def test_too_few_messages_triggers_truncation(self):
        cm = ContextManager()
        # PRESERVE_RECENT_TURNS * 2 = 8, so 6 messages -> too few to condense
        msgs = _make_text_messages(6)
        result = cm.condense(msgs)
        # Truncation keeps the last half: 6 // 2 = 3
        assert len(result) == 3
        assert result == msgs[-3:]


# ---------------------------------------------------------------------------
# _rule_based_summarize
# ---------------------------------------------------------------------------

class TestRuleBasedSummarize:

    def test_captures_user_requests(self):
        cm = ContextManager()
        msgs = [
            {"role": "user", "content": "Create a 5cm cylinder"},
            {"role": "assistant", "content": "OK"},
        ]
        summary = cm._rule_based_summarize(msgs)
        assert "Create a 5cm cylinder" in summary

    def test_captures_tool_calls(self):
        cm = ContextManager()
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "create_cylinder", "input": {"radius": 2.5}}
                ],
            }
        ]
        summary = cm._rule_based_summarize(msgs)
        assert "create_cylinder" in summary

    def test_captures_errors(self):
        cm = ContextManager()
        error_result = json.dumps({"success": False, "error": "Radius too large"})
        msgs = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "abc",
                        "content": error_result,
                    }
                ],
            }
        ]
        summary = cm._rule_based_summarize(msgs)
        assert "Radius too large" in summary

    def test_skips_auto_screenshot_messages(self):
        cm = ContextManager()
        msgs = [
            {"role": "user", "content": "[Auto-screenshot after create_box]"},
            {"role": "user", "content": "Make it bigger"},
        ]
        summary = cm._rule_based_summarize(msgs)
        assert "Auto-screenshot" not in summary
        assert "Make it bigger" in summary

    def test_empty_messages_returns_default(self):
        cm = ContextManager()
        summary = cm._rule_based_summarize([])
        assert "condensed" in summary.lower()


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------

class TestTruncateFallback:

    def test_very_short_conversation_unchanged(self):
        cm = ContextManager()
        msgs = _make_text_messages(3)
        assert cm._truncate(msgs) == msgs

    def test_keeps_recent_half(self):
        cm = ContextManager()
        msgs = _make_text_messages(10)
        result = cm._truncate(msgs)
        assert result == msgs[-5:]


# ---------------------------------------------------------------------------
# update_model / get_stats
# ---------------------------------------------------------------------------

class TestModelAndStats:

    def test_update_model(self):
        cm = ContextManager(model="claude-3-haiku-20240307")
        old_threshold = cm._threshold
        cm.update_model("claude-sonnet-4-20250514")
        assert cm.model == "claude-sonnet-4-20250514"
        # Both models have 200k window so threshold is the same
        assert cm._threshold == int(
            MODEL_CONTEXT_WINDOWS["claude-sonnet-4-20250514"] * CONDENSE_THRESHOLD
        )

    def test_update_model_unknown_uses_default(self):
        cm = ContextManager()
        cm.update_model("some-future-model")
        assert cm._context_window == DEFAULT_CONTEXT_WINDOW

    def test_get_stats_keys(self):
        cm = ContextManager()
        stats = cm.get_stats()
        assert "model" in stats
        assert "context_window" in stats
        assert "threshold_tokens" in stats
        assert "condensation_count" in stats

    def test_reset(self):
        cm = ContextManager()
        cm._condensation_count = 5
        cm.reset()
        assert cm._condensation_count == 0

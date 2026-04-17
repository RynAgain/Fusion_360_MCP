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
        # TASK-024: Images use a flat 1600-token cost regardless of base64 size
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
        assert cm.estimate_tokens(msgs) == 1600

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
        # TASK-024: text: 2 chars / 4 = 0 tokens, image: flat 1600 tokens
        assert cm.estimate_tokens(msgs) == 1600 + 2 // CHARS_PER_TOKEN


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


# ---------------------------------------------------------------------------
# _find_safe_split_point
# ---------------------------------------------------------------------------

class TestFindSafeSplitPoint:
    """Validate _find_safe_split_point avoids breaking tool_use/tool_result pairs."""

    def test_safe_split_at_regular_messages(self):
        """Split between two regular text messages returns same index."""
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Make a box"},
            {"role": "assistant", "content": "OK"},
        ]
        # Index 2 is a regular user text message -- safe
        result = ContextManager._find_safe_split_point(msgs, 2)
        assert result == 2

    def test_safe_split_moves_back_from_tool_result(self):
        """When split lands on a user message with tool_result content, move back."""
        msgs = [
            {"role": "user", "content": "Create something"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tc1", "name": "create_box", "input": {"width": 5}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tc1", "content": '{"success": true}'},
            ]},
            {"role": "assistant", "content": "Done!"},
        ]
        # Index 2 is a user(tool_result) message -- should move back
        result = ContextManager._find_safe_split_point(msgs, 2)
        # Should move back to index 1 (the assistant tool_use), then check
        # index 1 which is an assistant message (not a user tool_result), so
        # it stops there.
        assert result == 1

    def test_safe_split_moves_back_multiple_pairs(self):
        """Consecutive tool_use/tool_result pairs -- move back to safe boundary."""
        msgs = [
            {"role": "user", "content": "Build two things"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tc1", "name": "create_box", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tc1", "content": '{"success": true}'},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tc2", "name": "create_cylinder", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tc2", "content": '{"success": true}'},
            ]},
            {"role": "assistant", "content": "Both done!"},
        ]
        # Index 4 is user(tool_result) -> idx 3 is assistant(tool_use)
        # idx 3 is not a user tool_result so it stops.
        result = ContextManager._find_safe_split_point(msgs, 4)
        assert result == 3

    def test_safe_split_at_beginning(self):
        """Edge case where adjusting back would go below index 0."""
        msgs = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tc0", "content": '{"ok": true}'},
            ]},
            {"role": "assistant", "content": "Done"},
        ]
        result = ContextManager._find_safe_split_point(msgs, 0)
        assert result == 0

    def test_safe_split_with_text_user_message(self):
        """User message with plain text (not tool_result) at split point is safe."""
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tc1", "name": "create_box", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tc1", "content": '{"success": true}'},
            ]},
            {"role": "user", "content": "Now add a fillet"},
            {"role": "assistant", "content": "OK"},
        ]
        # Index 2 is a user message with plain text -- safe
        result = ContextManager._find_safe_split_point(msgs, 2)
        assert result == 2


# ---------------------------------------------------------------------------
# condense with design_state_summary
# ---------------------------------------------------------------------------

class TestCondenseWithDesignState:
    """Validate design_state_summary injection during condensation."""

    def test_condense_includes_design_state_in_summary(self):
        """Condense with design_state_summary includes it in the summary message."""
        cm = ContextManager()
        msgs = _make_text_messages(20)
        state_text = "Design State: 2 bodies [Body1, Body2], timeline pos 5, 1 component"
        result = cm.condense(msgs, design_state_summary=state_text)
        summary_content = result[0]["content"]
        assert "--- Current Design State ---" in summary_content
        assert state_text in summary_content

    def test_condense_without_design_state(self):
        """Condense without design_state_summary works as before (backwards compatible)."""
        cm = ContextManager()
        msgs = _make_text_messages(20)
        result = cm.condense(msgs)
        summary_content = result[0]["content"]
        assert "[Context Summary" in summary_content
        # Should NOT contain design state section
        assert "--- Current Design State ---" not in summary_content


# ---------------------------------------------------------------------------
# filter_operation_output
# ---------------------------------------------------------------------------

class TestFilterOperationOutput:
    """Validate context-window-friendly output filtering."""

    def test_short_output_passthrough(self):
        """Output under max_chars is returned as-is."""
        cm = ContextManager()
        short = "Operation completed successfully."
        assert cm.filter_operation_output(short) == short

    def test_long_output_truncation(self):
        """Output exceeding max_chars is truncated with head/tail preserved."""
        cm = ContextManager()
        long_output = "H" * 600 + "M" * 2000 + "T" * 600
        result = cm.filter_operation_output(long_output, max_chars=2000)
        assert "[... truncated" in result
        assert result.startswith("H" * 500)
        assert result.endswith("T" * 500)

    def test_extract_patterns_on_short_output(self):
        """Extract patterns appended even when output is short."""
        cm = ContextManager()
        output = "line1\nmetric: 42\nline3\n"
        result = cm.filter_operation_output(
            output, max_chars=5000, extract_patterns=[r"metric:"]
        )
        assert "--- Key Metrics ---" in result
        assert "metric: 42" in result

    def test_extract_patterns_on_long_output(self):
        """Extract patterns extracted from full output before truncation."""
        cm = ContextManager()
        lines = ["line " + str(i) for i in range(500)]
        lines[250] = "loss: 0.0042"
        lines[400] = "accuracy: 98.5%"
        output = "\n".join(lines)
        result = cm.filter_operation_output(
            output, max_chars=500,
            extract_patterns=[r"loss:", r"accuracy:"],
        )
        assert "[... truncated" in result
        assert "--- Key Metrics ---" in result
        assert "loss: 0.0042" in result
        assert "accuracy: 98.5%" in result

    def test_empty_output(self):
        """Empty string returns empty string."""
        cm = ContextManager()
        assert cm.filter_operation_output("") == ""

    def test_none_patterns_no_metrics_section(self):
        """When extract_patterns is None and output is short, no metrics section."""
        cm = ContextManager()
        result = cm.filter_operation_output("short text", max_chars=5000)
        assert "--- Key Metrics ---" not in result


# ---------------------------------------------------------------------------
# summarize_fusion_response
# ---------------------------------------------------------------------------

class TestSummarizeFusionResponse:
    """Validate compact Fusion 360 response summarisation."""

    def test_success_response(self):
        cm = ContextManager()
        resp = {
            "status": "simulation",
            "success": True,
            "body_name": "Body1",
            "feature_name": "Extrude1",
            "volume_cm3": 125.0,
            "face_count": 6,
        }
        summary = cm.summarize_fusion_response(resp)
        assert "success=True" in summary
        assert "body_name=Body1" in summary
        assert "feature_name=Extrude1" in summary
        assert "volume_cm3=125.0" in summary
        assert "face_count=6" in summary

    def test_error_response(self):
        cm = ContextManager()
        resp = {
            "status": "error",
            "success": False,
            "message": "Radius too large for fillet",
        }
        summary = cm.summarize_fusion_response(resp)
        assert "status=error" in summary
        assert "Radius too large" in summary

    def test_list_fields_counted(self):
        cm = ContextManager()
        resp = {
            "status": "simulation",
            "success": True,
            "bodies": [{"name": "B1"}, {"name": "B2"}, {"name": "B3"}],
            "count": 3,
        }
        summary = cm.summarize_fusion_response(resp)
        assert "bodies=[3 items]" in summary
        assert "count=3" in summary

    def test_empty_response(self):
        cm = ContextManager()
        assert cm.summarize_fusion_response({}) == "<empty response>"

    def test_none_response(self):
        cm = ContextManager()
        assert cm.summarize_fusion_response(None) == "<empty response>"

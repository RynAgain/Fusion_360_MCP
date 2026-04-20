"""
tests/test_context_manager.py
Unit tests for ai.context_manager -- context estimation, condensation,
truncation, and statistics.
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from ai.context_manager import (
    CHARS_PER_TOKEN,
    CONDENSE_THRESHOLD,
    ContextManager,
    DEFAULT_CONTEXT_WINDOW,
    MODEL_CONTEXT_WINDOWS,
    PRESERVE_RECENT_TURNS,
    TruncationResult,
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


# ---------------------------------------------------------------------------
# TASK-180: Configurable condensation thresholds
# ---------------------------------------------------------------------------

class TestConfigurableThresholds:
    """Verify that custom condense_threshold / preserve_recent_turns from
    settings (or constructor args) are respected."""

    def test_default_values_match_existing_behaviour(self):
        """When no overrides are given, defaults match the module constants."""
        cm = ContextManager(
            condense_threshold=CONDENSE_THRESHOLD,
            preserve_recent_turns=PRESERVE_RECENT_TURNS,
            condense_strategy="hybrid",
        )
        assert cm._condense_threshold == CONDENSE_THRESHOLD
        assert cm._preserve_recent_turns == PRESERVE_RECENT_TURNS
        assert cm._condense_strategy == "hybrid"
        assert cm._threshold == int(
            MODEL_CONTEXT_WINDOWS["claude-sonnet-4-20250514"] * CONDENSE_THRESHOLD
        )

    def test_custom_threshold_changes_trigger_point(self):
        """A higher condense_threshold raises the trigger point."""
        cm = ContextManager(
            condense_threshold=0.90,
            preserve_recent_turns=PRESERVE_RECENT_TURNS,
            condense_strategy="hybrid",
        )
        expected = int(
            MODEL_CONTEXT_WINDOWS["claude-sonnet-4-20250514"] * 0.90
        )
        assert cm._threshold == expected
        # Should NOT condense at default 65% level
        chars_65 = int(DEFAULT_CONTEXT_WINDOW * 0.65) * CHARS_PER_TOKEN + 1000
        msgs = [{"role": "user", "content": "x" * chars_65}]
        assert cm.should_condense(msgs) is False

    def test_custom_preserve_recent_turns(self):
        """Custom preserve_recent_turns changes how many messages are kept."""
        cm = ContextManager(
            condense_threshold=CONDENSE_THRESHOLD,
            preserve_recent_turns=2,
            condense_strategy="hybrid",
        )
        msgs = _make_text_messages(20)
        result = cm.condense(msgs)
        # 2 turns * 2 messages/turn = 4 recent messages + 1 summary
        assert len(result) == 1 + 4
        assert result[1:] == msgs[-4:]

    def test_custom_preserve_recent_turns_large(self):
        """Large preserve_recent_turns keeps more messages."""
        cm = ContextManager(
            condense_threshold=CONDENSE_THRESHOLD,
            preserve_recent_turns=6,
            condense_strategy="hybrid",
        )
        msgs = _make_text_messages(20)
        result = cm.condense(msgs)
        # 6 turns * 2 messages/turn = 12 recent + 1 summary
        assert len(result) == 1 + 12
        assert result[1:] == msgs[-12:]

    def test_settings_integration(self):
        """ContextManager reads from settings when no args are given."""
        mock_settings = MagicMock()
        mock_settings.get.side_effect = lambda key, fallback=None: {
            "condense_threshold": 0.80,
            "condense_preserve_recent_turns": 3,
            "condense_strategy": "rule_based",
        }.get(key, fallback)

        with patch("ai.context_manager.settings", mock_settings, create=True):
            # Patch the lazy import inside __init__
            with patch.dict("sys.modules", {}):
                import importlib
                # Use direct constructor with None to trigger settings read
                with patch(
                    "config.settings.settings", mock_settings
                ):
                    cm = ContextManager()

        assert cm._condense_threshold == 0.80
        assert cm._preserve_recent_turns == 3
        assert cm._condense_strategy == "rule_based"

    def test_update_model_uses_instance_threshold(self):
        """update_model recalculates using the instance threshold, not the
        module constant."""
        cm = ContextManager(
            condense_threshold=0.50,
            preserve_recent_turns=PRESERVE_RECENT_TURNS,
            condense_strategy="hybrid",
        )
        cm.update_model("claude-3-haiku-20240307")
        expected = int(MODEL_CONTEXT_WINDOWS["claude-3-haiku-20240307"] * 0.50)
        assert cm._threshold == expected

    def test_condense_strategy_stored(self):
        """condense_strategy is stored on the instance."""
        for strategy in ("llm", "rule_based", "hybrid"):
            cm = ContextManager(
                condense_threshold=CONDENSE_THRESHOLD,
                preserve_recent_turns=PRESERVE_RECENT_TURNS,
                condense_strategy=strategy,
            )
            assert cm._condense_strategy == strategy


# ---------------------------------------------------------------------------
# TASK-162: Non-destructive truncation
# ---------------------------------------------------------------------------

class TestTruncateNondestructive:
    """Validate truncate_nondestructive() tags messages instead of deleting."""

    def test_hides_correct_number_of_messages(self):
        """Approximately half the eligible messages are hidden."""
        cm = ContextManager()
        msgs = _make_text_messages(10)
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        # 10 messages, first is kept, 9 eligible, 50% = 4 (rounded to even)
        assert result.messages_hidden == 4
        assert isinstance(result, TruncationResult)

    def test_first_message_always_retained(self):
        """The first message is never tagged as hidden."""
        cm = ContextManager()
        msgs = _make_text_messages(10)
        cm.truncate_nondestructive(msgs, frac_to_remove=0.9)
        assert "_is_hidden" not in msgs[0]
        assert "_truncation_parent" not in msgs[0]

    def test_hidden_messages_have_correct_metadata(self):
        """Hidden messages have _truncation_parent and _is_hidden flags."""
        cm = ContextManager()
        msgs = _make_text_messages(10)
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        hidden = [m for m in msgs if m.get("_is_hidden")]
        assert len(hidden) == result.messages_hidden
        for m in hidden:
            assert m["_truncation_parent"] == result.truncation_id
            assert m["_is_hidden"] is True

    def test_truncation_marker_inserted(self):
        """A truncation marker message is inserted after the hidden messages."""
        cm = ContextManager()
        msgs = _make_text_messages(10)
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        markers = [m for m in msgs if m.get("_is_truncation_marker")]
        assert len(markers) == 1
        marker = markers[0]
        assert marker["role"] == "user"
        assert "[Context truncated" in marker["content"]
        assert marker["_truncation_id"] == result.truncation_id

    def test_truncation_id_is_uuid(self):
        """The truncation_id should be a valid UUID string."""
        import uuid
        cm = ContextManager()
        msgs = _make_text_messages(10)
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        # Should not raise
        uuid.UUID(result.truncation_id)

    def test_empty_messages_returns_zero_hidden(self):
        cm = ContextManager()
        msgs = []
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        assert result.messages_hidden == 0

    def test_single_message_returns_zero_hidden(self):
        cm = ContextManager()
        msgs = [{"role": "user", "content": "Hello"}]
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        assert result.messages_hidden == 0
        assert len(msgs) == 1

    def test_hide_count_rounded_to_even(self):
        """The number of hidden messages is always even (to keep pairs)."""
        cm = ContextManager()
        # 7 messages -> 6 eligible -> 50% = 3 -> rounded to 2
        msgs = _make_text_messages(7)
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        assert result.messages_hidden % 2 == 0

    def test_messages_retained_matches_visible_count(self):
        """TASK-201: messages_retained should equal the number of visible messages."""
        cm = ContextManager()
        msgs = _make_text_messages(10)
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        # Count actually visible messages (not hidden and not truncation markers)
        visible_count = sum(
            1 for m in msgs
            if not m.get("_is_hidden") and not m.get("_is_truncation_marker")
        )
        assert result.messages_retained == visible_count

    def test_messages_retained_excludes_marker(self):
        """TASK-201: messages_retained must not count the truncation marker."""
        cm = ContextManager()
        msgs = _make_text_messages(12)
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        # Total messages = original + 1 marker inserted
        # visible = total - hidden - markers
        total = len(msgs)
        hidden = result.messages_hidden
        markers = sum(1 for m in msgs if m.get("_is_truncation_marker"))
        expected_retained = total - hidden - markers
        assert result.messages_retained == expected_retained

    def test_messages_retained_zero_hidden(self):
        """TASK-201: When nothing is hidden, messages_retained equals total count."""
        cm = ContextManager()
        msgs = [{"role": "user", "content": "Only one"}]
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        assert result.messages_hidden == 0
        assert result.messages_retained == len(msgs)


class TestGetVisibleMessages:
    """Validate get_visible_messages() filters hidden messages."""

    def test_filters_hidden_messages(self):
        cm = ContextManager()
        msgs = _make_text_messages(10)
        cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        visible = cm.get_visible_messages(msgs)
        assert all("_is_hidden" not in m for m in visible)

    def test_filters_truncation_markers(self):
        cm = ContextManager()
        msgs = _make_text_messages(10)
        cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        visible = cm.get_visible_messages(msgs)
        assert all("_is_truncation_marker" not in m for m in visible)

    def test_first_message_always_included(self):
        cm = ContextManager()
        msgs = _make_text_messages(10)
        cm.truncate_nondestructive(msgs, frac_to_remove=0.9)
        visible = cm.get_visible_messages(msgs)
        assert visible[0] == msgs[0]

    def test_empty_list_returns_empty(self):
        assert ContextManager.get_visible_messages([]) == []

    def test_no_hidden_messages_returns_all(self):
        msgs = _make_text_messages(5)
        visible = ContextManager.get_visible_messages(msgs)
        assert visible == msgs


class TestRestoreTruncated:
    """Validate restore_truncated() un-hides messages by truncation_id."""

    def test_restores_hidden_messages(self):
        cm = ContextManager()
        msgs = _make_text_messages(10)
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        restored_count = cm.restore_truncated(msgs, result.truncation_id)
        assert restored_count == result.messages_hidden
        # No hidden messages should remain for this truncation_id
        hidden = [m for m in msgs if m.get("_truncation_parent") == result.truncation_id]
        assert len(hidden) == 0

    def test_restore_returns_correct_count(self):
        cm = ContextManager()
        msgs = _make_text_messages(10)
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        count = cm.restore_truncated(msgs, result.truncation_id)
        assert count == result.messages_hidden

    def test_restore_removes_truncation_marker(self):
        cm = ContextManager()
        msgs = _make_text_messages(10)
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        cm.restore_truncated(msgs, result.truncation_id)
        markers = [m for m in msgs if m.get("_is_truncation_marker")]
        assert len(markers) == 0

    def test_restore_with_invalid_id_returns_zero(self):
        cm = ContextManager()
        msgs = _make_text_messages(10)
        cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        count = cm.restore_truncated(msgs, "nonexistent-id")
        assert count == 0

    def test_restored_messages_are_visible(self):
        cm = ContextManager()
        msgs = _make_text_messages(10)
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        visible_before = cm.get_visible_messages(msgs)
        cm.restore_truncated(msgs, result.truncation_id)
        visible_after = cm.get_visible_messages(msgs)
        assert len(visible_after) > len(visible_before)

    def test_multiple_truncation_rounds(self):
        """Nested truncation IDs -- restore one without affecting the other."""
        cm = ContextManager()
        msgs = _make_text_messages(20)
        # First truncation round
        r1 = cm.truncate_nondestructive(msgs, frac_to_remove=0.3)
        # Second truncation round on the same list
        r2 = cm.truncate_nondestructive(msgs, frac_to_remove=0.3)
        assert r1.truncation_id != r2.truncation_id

        # Restore only the second round
        count2 = cm.restore_truncated(msgs, r2.truncation_id)
        assert count2 == r2.messages_hidden

        # First round's hidden messages should still be hidden
        hidden_r1 = [m for m in msgs if m.get("_truncation_parent") == r1.truncation_id]
        assert len(hidden_r1) == r1.messages_hidden

        # Restore first round
        count1 = cm.restore_truncated(msgs, r1.truncation_id)
        assert count1 == r1.messages_hidden

        # No hidden messages remain
        hidden = [m for m in msgs if m.get("_is_hidden")]
        assert len(hidden) == 0

    def test_full_roundtrip_preserves_content(self):
        """After truncate + restore, original message content is intact."""
        cm = ContextManager()
        msgs = _make_text_messages(8)
        original_contents = [m["content"] for m in msgs]
        result = cm.truncate_nondestructive(msgs, frac_to_remove=0.5)
        cm.restore_truncated(msgs, result.truncation_id)
        # After restore and marker removal, contents match original
        current_contents = [m["content"] for m in msgs]
        assert current_contents == original_contents

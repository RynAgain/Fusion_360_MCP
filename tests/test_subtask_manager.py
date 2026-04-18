"""Tests for ai/subtask_manager.py -- SubtaskManager."""

import copy
import unittest
from unittest.mock import MagicMock, patch

from ai.context_bridge import ContextBridge, SubtaskContext
from ai.subtask_manager import (
    OrchestratorState,
    SubtaskManager,
    SubtaskResult,
    SubtaskStatus,
)
from ai.task_manager import TaskManager


# ---------------------------------------------------------------------------
# Mock / Fake helpers
# ---------------------------------------------------------------------------


class MockModeManager:
    """Minimal mock of ModeManager for subtask testing."""

    def __init__(self, active_mode: str = "full"):
        self._active_mode = active_mode

    def switch_mode(self, slug: str):
        self._active_mode = slug
        # Return a mock CadMode
        mode = MagicMock()
        mode.slug = slug
        mode.name = slug.title()
        return mode


class MockSettings:
    """Minimal mock of Settings for ClaudeClient."""

    def __init__(self):
        self.system_prompt = "user additions"
        self.model = "test-model"
        self.max_tokens = 1024


class MockClient:
    """Minimal mock of ClaudeClient for subtask testing."""

    def __init__(self):
        self.conversation_history = []
        self._system_prompt = "base system prompt"
        self.mode_manager = MockModeManager("full")
        self.settings = MockSettings()
        self._run_turn_called_with = None

    def get_conversation_snapshot(self):
        """Return a copy of conversation history (mirrors ClaudeClient)."""
        return list(self.conversation_history)

    def get_system_prompt(self):
        """Return the current system prompt (mirrors ClaudeClient)."""
        return self._system_prompt

    def get_active_mode(self):
        """Return the active mode slug (mirrors ClaudeClient)."""
        return self.mode_manager._active_mode

    def run_turn(self, message, on_event=None):
        """Simulate an agentic turn."""
        self._run_turn_called_with = message
        # Add the user message
        self.conversation_history.append({"role": "user", "content": message})
        # Simulate an assistant response with tool use and text
        self.conversation_history.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "create_sketch",
                        "input": {"plane": "XY"},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Step completed successfully. "
                            "Created the base sketch with a 50mm circle."
                        ),
                    },
                ],
            }
        )
        # Simulate tool result
        self.conversation_history.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_1",
                        "content": '{"success": true}',
                    }
                ],
            }
        )
        # Emit tool_call event if callback provided
        if on_event:
            on_event("tool_call", {"tool_name": "create_sketch"})


class FailingClient(MockClient):
    """Client whose run_turn raises an exception."""

    def run_turn(self, message, on_event=None):
        raise RuntimeError("LLM API call failed")


# ---------------------------------------------------------------------------
# Helper to create a TaskManager with an orchestrated plan
# ---------------------------------------------------------------------------


def _make_task_manager():
    """Return a TaskManager with a small orchestrated plan."""
    tm = TaskManager()
    tm.create_orchestrated_plan(
        "Test Design",
        [
            {"description": "Create base sketch", "mode_hint": "sketch"},
            {
                "description": "Extrude base",
                "mode_hint": "modeling",
                "depends_on": [0],
            },
            {
                "description": "Add fillets",
                "mode_hint": "modeling",
                "depends_on": [1],
            },
        ],
    )
    return tm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSubtaskResultSerialization(unittest.TestCase):
    """SubtaskResult.to_dict() serialization."""

    def test_to_dict_completed(self):
        r = SubtaskResult(
            step_index=0,
            status=SubtaskStatus.COMPLETED,
            result_text="Done",
            mode_used="sketch",
            messages_exchanged=4,
            tool_calls_made=2,
            duration_seconds=1.5,
        )
        d = r.to_dict()
        self.assertEqual(d["step_index"], 0)
        self.assertEqual(d["status"], "completed")
        self.assertEqual(d["result_text"], "Done")
        self.assertEqual(d["mode_used"], "sketch")
        self.assertEqual(d["messages_exchanged"], 4)
        self.assertEqual(d["tool_calls_made"], 2)
        self.assertAlmostEqual(d["duration_seconds"], 1.5)
        self.assertIsNone(d["error"])

    def test_to_dict_failed(self):
        r = SubtaskResult(
            step_index=1,
            status=SubtaskStatus.FAILED,
            result_text="",
            mode_used="modeling",
            messages_exchanged=0,
            tool_calls_made=0,
            duration_seconds=0.1,
            error="boom",
        )
        d = r.to_dict()
        self.assertEqual(d["status"], "failed")
        self.assertEqual(d["error"], "boom")


class TestSnapshotState(unittest.TestCase):
    """SubtaskManager.snapshot_state() captures state correctly."""

    def test_snapshot_captures_all_fields(self):
        client = MockClient()
        client.conversation_history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
        ]
        client._system_prompt = "my prompt"
        client.mode_manager._active_mode = "sketch"

        mgr = SubtaskManager()
        snap = mgr.snapshot_state(client)

        self.assertEqual(snap.system_prompt, "my prompt")
        self.assertEqual(snap.active_mode, "sketch")
        self.assertEqual(len(snap.conversation_history), 2)

    def test_snapshot_deep_copies_messages(self):
        client = MockClient()
        client.conversation_history = [
            {"role": "user", "content": "Hello"}
        ]

        mgr = SubtaskManager()
        snap = mgr.snapshot_state(client)

        # Mutate original -- snapshot should be unaffected
        client.conversation_history.append({"role": "assistant", "content": "X"})
        client.conversation_history[0]["content"] = "Changed"

        self.assertEqual(len(snap.conversation_history), 1)
        self.assertEqual(snap.conversation_history[0]["content"], "Hello")


class TestRestoreState(unittest.TestCase):
    """SubtaskManager.restore_state() restores state completely."""

    def test_restore_replaces_all_fields(self):
        client = MockClient()
        client.conversation_history = [{"role": "user", "content": "Subtask msg"}]
        client._system_prompt = "subtask prompt"
        client.mode_manager._active_mode = "modeling"

        state = OrchestratorState(
            conversation_history=[{"role": "user", "content": "Original"}],
            system_prompt="original prompt",
            active_mode="full",
        )

        mgr = SubtaskManager()
        mgr.restore_state(client, state)

        self.assertEqual(client.conversation_history, [{"role": "user", "content": "Original"}])
        self.assertEqual(client._system_prompt, "original prompt")
        self.assertEqual(client.mode_manager._active_mode, "full")

    def test_restore_skips_mode_switch_if_same(self):
        client = MockClient()
        client.mode_manager._active_mode = "sketch"

        state = OrchestratorState(
            conversation_history=[],
            system_prompt="p",
            active_mode="sketch",
        )

        mgr = SubtaskManager()
        # Wrap switch_mode to check it is NOT called
        original = client.mode_manager.switch_mode
        call_log = []
        def tracking_switch(slug):
            call_log.append(slug)
            return original(slug)
        client.mode_manager.switch_mode = tracking_switch

        mgr.restore_state(client, state)
        self.assertEqual(call_log, [])


class TestPrepareSubtask(unittest.TestCase):
    """SubtaskManager.prepare_subtask() clears history, switches mode, updates prompt."""

    @patch("ai.subtask_manager.build_system_prompt", return_value="mode prompt for sketch")
    def test_prepare_clears_and_switches(self, mock_build):
        client = MockClient()
        client.conversation_history = [
            {"role": "user", "content": "Old message"},
        ]
        client.mode_manager._active_mode = "full"

        context = SubtaskContext(
            step_index=0,
            step_description="Create base sketch",
            mode="sketch",
            plan_title="Test",
            plan_summary="summary",
            instructions="Draw a circle",
        )

        mgr = SubtaskManager()
        mgr.prepare_subtask(client, context)

        # History cleared
        self.assertEqual(client.conversation_history, [])
        # Mode switched
        self.assertEqual(client.mode_manager._active_mode, "sketch")
        # System prompt rebuilt with context appended
        self.assertIn("mode prompt for sketch", client._system_prompt)
        self.assertIn("Orchestrated Subtask", client._system_prompt)
        self.assertIn("Create base sketch", client._system_prompt)
        # build_system_prompt called with correct args
        mock_build.assert_called_once_with(
            user_additions="user additions", mode="sketch"
        )


class TestExecuteSubtaskSuccess(unittest.TestCase):
    """Full happy-path execution."""

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_happy_path(self, _mock_build):
        client = MockClient()
        client.conversation_history = [{"role": "user", "content": "Original"}]
        client._system_prompt = "original prompt"
        client.mode_manager._active_mode = "full"

        tm = _make_task_manager()
        bridge = ContextBridge()
        mgr = SubtaskManager(context_bridge=bridge)

        result = mgr.execute_subtask(client, tm, step_index=0)

        # Result is completed
        self.assertEqual(result.status, SubtaskStatus.COMPLETED)
        self.assertEqual(result.step_index, 0)
        self.assertEqual(result.mode_used, "sketch")
        self.assertGreater(result.duration_seconds, 0)
        self.assertIn("Step completed successfully", result.result_text)
        self.assertGreaterEqual(result.tool_calls_made, 1)
        self.assertGreater(result.messages_exchanged, 0)
        self.assertIsNone(result.error)

        # TaskManager updated
        self.assertEqual(tm._tasks[0].status.value, "completed")

        # ContextBridge recorded result
        self.assertIn(0, bridge.recorded_results)

        # Orchestrator state restored
        self.assertEqual(
            client.conversation_history,
            [{"role": "user", "content": "Original"}],
        )
        self.assertEqual(client._system_prompt, "original prompt")
        self.assertEqual(client.mode_manager._active_mode, "full")

        # Execution history tracked
        self.assertEqual(len(mgr.execution_history), 1)


class TestExecuteSubtaskFailure(unittest.TestCase):
    """run_turn raises -- state must still be restored."""

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_failure_restores_state(self, _mock_build):
        client = FailingClient()
        client.conversation_history = [{"role": "user", "content": "Original"}]
        client._system_prompt = "original prompt"
        client.mode_manager._active_mode = "full"

        tm = _make_task_manager()
        mgr = SubtaskManager()

        result = mgr.execute_subtask(client, tm, step_index=0)

        # Result is failed
        self.assertEqual(result.status, SubtaskStatus.FAILED)
        self.assertIn("LLM API call failed", result.error)

        # TaskManager marked as failed
        self.assertEqual(tm._tasks[0].status.value, "failed")

        # Orchestrator state RESTORED even on failure
        self.assertEqual(
            client.conversation_history,
            [{"role": "user", "content": "Original"}],
        )
        self.assertEqual(client._system_prompt, "original prompt")
        self.assertEqual(client.mode_manager._active_mode, "full")

        # Not currently executing
        self.assertFalse(mgr.is_executing)
        self.assertIsNone(mgr.current_step)


class TestExecuteSubtaskAlreadyRunning(unittest.TestCase):
    """Raises RuntimeError if already executing."""

    def test_raises_runtime_error(self):
        mgr = SubtaskManager()
        mgr._is_executing = True

        with self.assertRaises(RuntimeError) as ctx:
            mgr.execute_subtask(MockClient(), _make_task_manager())
        self.assertIn("already executing", str(ctx.exception))


class TestExecuteSubtaskNoStepAvailable(unittest.TestCase):
    """Raises ValueError if no step is available."""

    def test_raises_value_error_empty_plan(self):
        mgr = SubtaskManager()
        tm = TaskManager()  # no plan

        with self.assertRaises(ValueError):
            mgr.execute_subtask(MockClient(), tm)

    def test_raises_value_error_all_completed(self):
        tm = _make_task_manager()
        tm.complete_step(0, "done")
        tm.complete_step(1, "done")
        tm.complete_step(2, "done")

        mgr = SubtaskManager()
        with self.assertRaises(ValueError):
            mgr.execute_subtask(MockClient(), tm)


class TestExtractResult(unittest.TestCase):
    """_extract_result extracts text and counts correctly."""

    def setUp(self):
        self.mgr = SubtaskManager()

    def test_extract_text_response(self):
        history = [
            {"role": "user", "content": "Do the thing"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "First response"},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Final answer here"},
                ],
            },
        ]
        text, tools, msgs = self.mgr._extract_result(history)
        self.assertEqual(text, "Final answer here")
        self.assertEqual(tools, 0)
        self.assertEqual(msgs, 3)

    def test_extract_with_tool_calls(self):
        history = [
            {"role": "user", "content": "Create cylinder"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "create_cylinder",
                        "input": {},
                    },
                    {"type": "text", "text": "Created it."},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": '{"success": true}',
                    }
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "take_screenshot",
                        "input": {},
                    },
                ],
            },
        ]
        text, tools, msgs = self.mgr._extract_result(history)
        # Last assistant text is "Created it." (second assistant has no text)
        self.assertEqual(text, "Created it.")
        self.assertEqual(tools, 2)  # Two tool_use blocks
        self.assertEqual(msgs, 4)

    def test_extract_empty_conversation(self):
        text, tools, msgs = self.mgr._extract_result([])
        self.assertEqual(text, "")
        self.assertEqual(tools, 0)
        self.assertEqual(msgs, 0)

    def test_extract_string_content(self):
        """Handle assistant content as a plain string."""
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "plain string response"},
        ]
        text, tools, msgs = self.mgr._extract_result(history)
        self.assertEqual(text, "plain string response")
        self.assertEqual(tools, 0)
        self.assertEqual(msgs, 2)

    def test_extract_tool_result_fallback(self):
        """When no assistant text, falls back to last tool result."""
        history = [
            {"role": "user", "content": "Do it"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "create_sketch",
                        "input": {},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": '{"success": true, "sketch_name": "Sketch1"}',
                    }
                ],
            },
        ]
        text, tools, msgs = self.mgr._extract_result(history)
        self.assertIn("[Tool result]", text)
        self.assertIn("success", text)
        self.assertEqual(tools, 1)


class TestExecutionHistory(unittest.TestCase):
    """Tracks all executions in history."""

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_history_accumulates(self, _mock_build):
        tm = _make_task_manager()
        mgr = SubtaskManager()

        # Execute step 0
        r1 = mgr.execute_subtask(MockClient(), tm, step_index=0)
        self.assertEqual(len(mgr.execution_history), 1)

        # Execute step 1 (depends on 0, which is now completed)
        r2 = mgr.execute_subtask(MockClient(), tm, step_index=1)
        self.assertEqual(len(mgr.execution_history), 2)

        # Both recorded
        self.assertEqual(mgr.execution_history[0].step_index, 0)
        self.assertEqual(mgr.execution_history[1].step_index, 1)

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_history_is_copy(self, _mock_build):
        """execution_history property returns a copy."""
        tm = _make_task_manager()
        mgr = SubtaskManager()
        mgr.execute_subtask(MockClient(), tm, step_index=0)

        hist = mgr.execution_history
        hist.clear()  # mutate the copy
        self.assertEqual(len(mgr.execution_history), 1)  # original unchanged


class TestGetExecutionSummary(unittest.TestCase):
    """get_execution_summary() aggregation."""

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_summary_after_mixed_executions(self, _mock_build):
        tm = _make_task_manager()
        mgr = SubtaskManager()

        # Step 0 succeeds
        mgr.execute_subtask(MockClient(), tm, step_index=0)
        # Step 1 fails
        mgr.execute_subtask(FailingClient(), tm, step_index=1)

        summary = mgr.get_execution_summary()
        self.assertEqual(summary["total_executed"], 2)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertGreater(summary["total_duration"], 0)
        self.assertGreaterEqual(summary["total_tool_calls"], 1)
        self.assertEqual(len(summary["executions"]), 2)

    def test_summary_empty(self):
        mgr = SubtaskManager()
        summary = mgr.get_execution_summary()
        self.assertEqual(summary["total_executed"], 0)
        self.assertEqual(summary["completed"], 0)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["total_duration"], 0)
        self.assertEqual(summary["total_tool_calls"], 0)
        self.assertEqual(summary["executions"], [])


class TestClear(unittest.TestCase):
    """clear() clears everything."""

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_clear_resets_all(self, _mock_build):
        tm = _make_task_manager()
        bridge = ContextBridge()
        mgr = SubtaskManager(context_bridge=bridge)

        mgr.execute_subtask(MockClient(), tm, step_index=0)
        self.assertEqual(len(mgr.execution_history), 1)
        self.assertIn(0, bridge.recorded_results)

        mgr.clear()

        self.assertEqual(len(mgr.execution_history), 0)
        self.assertEqual(bridge.recorded_results, {})
        self.assertFalse(mgr.is_executing)
        self.assertIsNone(mgr.current_step)


class TestStateAlwaysRestoredOnError(unittest.TestCase):
    """Verify try/finally restores state even on unexpected errors."""

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_state_restored_on_error(self, _mock_build):
        client = FailingClient()
        original_history = [{"role": "user", "content": "Keep me"}]
        client.conversation_history = copy.deepcopy(original_history)
        client._system_prompt = "keep this prompt"
        client.mode_manager._active_mode = "full"

        tm = _make_task_manager()
        mgr = SubtaskManager()

        result = mgr.execute_subtask(client, tm, step_index=0)

        # Verify state is fully restored
        self.assertEqual(client.conversation_history, original_history)
        self.assertEqual(client._system_prompt, "keep this prompt")
        self.assertEqual(client.mode_manager._active_mode, "full")
        self.assertFalse(mgr.is_executing)
        self.assertIsNone(mgr.current_step)
        self.assertEqual(result.status, SubtaskStatus.FAILED)


class TestEmitCallbackEvents(unittest.TestCase):
    """Correct events emitted at lifecycle points."""

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_success_events(self, _mock_build):
        client = MockClient()
        tm = _make_task_manager()
        mgr = SubtaskManager()

        events = []

        def collector(event_name, data):
            events.append((event_name, data))

        mgr.execute_subtask(
            client, tm, step_index=0, emit_callback=collector
        )

        event_names = [e[0] for e in events]
        self.assertIn("subtask_started", event_names)
        self.assertIn("subtask_completed", event_names)
        self.assertNotIn("subtask_failed", event_names)

        # Check started event data
        started = next(d for n, d in events if n == "subtask_started")
        self.assertEqual(started["step_index"], 0)
        self.assertEqual(started["mode"], "sketch")
        self.assertEqual(started["description"], "Create base sketch")

        # Check completed event data
        completed = next(d for n, d in events if n == "subtask_completed")
        self.assertEqual(completed["step_index"], 0)
        self.assertIn("result", completed)
        self.assertIn("duration", completed)

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_failure_events(self, _mock_build):
        client = FailingClient()
        tm = _make_task_manager()
        mgr = SubtaskManager()

        events = []

        def collector(event_name, data):
            events.append((event_name, data))

        mgr.execute_subtask(
            client, tm, step_index=0, emit_callback=collector
        )

        event_names = [e[0] for e in events]
        self.assertIn("subtask_started", event_names)
        self.assertIn("subtask_failed", event_names)
        self.assertNotIn("subtask_completed", event_names)

        # Check failed event data
        failed = next(d for n, d in events if n == "subtask_failed")
        self.assertEqual(failed["step_index"], 0)
        self.assertIn("LLM API call failed", failed["error"])
        self.assertIn("duration", failed)

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_no_callback_no_error(self, _mock_build):
        """emit_callback=None should not cause errors."""
        client = MockClient()
        tm = _make_task_manager()
        mgr = SubtaskManager()

        result = mgr.execute_subtask(client, tm, step_index=0, emit_callback=None)
        self.assertEqual(result.status, SubtaskStatus.COMPLETED)


class TestAutoAdvance(unittest.TestCase):
    """execute_subtask with step_index=None uses auto_advance."""

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_auto_advance_picks_first_ready(self, _mock_build):
        tm = _make_task_manager()
        mgr = SubtaskManager()

        # Step 0 has no dependencies, should auto-advance to it
        result = mgr.execute_subtask(MockClient(), tm, step_index=None)
        self.assertEqual(result.step_index, 0)

    @patch("ai.subtask_manager.build_system_prompt", return_value="prompt")
    def test_auto_advance_respects_dependencies(self, _mock_build):
        tm = _make_task_manager()
        # Complete step 0 first
        tm.start_step(0)
        tm.complete_step(0, "done")

        mgr = SubtaskManager()
        result = mgr.execute_subtask(MockClient(), tm, step_index=None)
        # Step 1 depends on 0 (now completed), so should be picked
        self.assertEqual(result.step_index, 1)


class TestProperties(unittest.TestCase):
    """Property accessors behave correctly."""

    def test_initial_state(self):
        mgr = SubtaskManager()
        self.assertFalse(mgr.is_executing)
        self.assertIsNone(mgr.current_step)
        self.assertEqual(mgr.execution_history, [])
        self.assertIsInstance(mgr.context_bridge, ContextBridge)

    def test_custom_context_bridge(self):
        bridge = ContextBridge(token_budget=2000)
        mgr = SubtaskManager(context_bridge=bridge)
        self.assertIs(mgr.context_bridge, bridge)


class TestOrchestratorState(unittest.TestCase):
    """OrchestratorState dataclass."""

    def test_fields(self):
        state = OrchestratorState(
            conversation_history=[{"role": "user", "content": "hi"}],
            system_prompt="prompt",
            active_mode="sketch",
        )
        self.assertEqual(len(state.conversation_history), 1)
        self.assertEqual(state.system_prompt, "prompt")
        self.assertEqual(state.active_mode, "sketch")


class TestSubtaskStatusEnum(unittest.TestCase):
    """SubtaskStatus enum values."""

    def test_values(self):
        self.assertEqual(SubtaskStatus.PENDING.value, "pending")
        self.assertEqual(SubtaskStatus.RUNNING.value, "running")
        self.assertEqual(SubtaskStatus.COMPLETED.value, "completed")
        self.assertEqual(SubtaskStatus.FAILED.value, "failed")
        self.assertEqual(SubtaskStatus.RETRYING.value, "retrying")


if __name__ == "__main__":
    unittest.main()

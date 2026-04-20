"""Tests for ai/checkpoint_manager.py -- design checkpoint system."""
import pytest
from unittest.mock import MagicMock

from ai.checkpoint_manager import CheckpointManager, DesignCheckpoint


@pytest.fixture
def mock_mcp():
    """Create a mock MCP server with configurable tool responses."""
    server = MagicMock()

    def execute_tool(name, params):
        if name == 'get_timeline':
            return {
                'success': True,
                'timeline': [
                    {'index': 0, 'name': 'Sketch1'},
                    {'index': 1, 'name': 'Extrusion1'},
                ],
            }
        elif name == 'get_body_list':
            return {'success': True, 'count': 3}
        elif name == 'undo':
            return {'success': True}
        return {'success': False}

    server.execute_tool = MagicMock(side_effect=execute_tool)
    return server


@pytest.fixture
def manager():
    return CheckpointManager()


class TestDesignCheckpoint:
    """Tests for the DesignCheckpoint data class."""

    def test_to_dict(self):
        cp = DesignCheckpoint(
            name="test", timeline_position=5, body_count=2,
            message_index=10, description="a test checkpoint",
        )
        d = cp.to_dict()
        assert d['name'] == 'test'
        assert d['timeline_position'] == 5
        assert d['body_count'] == 2
        assert d['message_index'] == 10
        assert d['description'] == 'a test checkpoint'
        assert 'created_at' in d


class TestCheckpointManager:
    """Tests for CheckpointManager."""

    def test_save_checkpoint(self, manager, mock_mcp):
        cp = manager.save("v1", mock_mcp, message_count=5, description="first version")
        assert cp.name == "v1"
        assert cp.timeline_position == 2  # 2 items in mock timeline
        assert cp.body_count == 3
        assert cp.message_index == 5
        assert cp.description == "first version"
        assert manager.count == 1

    def test_list_checkpoints(self, manager, mock_mcp):
        manager.save("v1", mock_mcp, message_count=3)
        manager.save("v2", mock_mcp, message_count=7)
        result = manager.list_all()
        assert len(result) == 2
        assert result[0]['name'] == 'v1'
        assert result[1]['name'] == 'v2'

    def test_get_checkpoint(self, manager, mock_mcp):
        manager.save("v1", mock_mcp, message_count=3)
        cp = manager.get("v1")
        assert cp is not None
        assert cp.name == "v1"

    def test_get_nonexistent(self, manager):
        result = manager.get("missing")
        assert result is None

    def test_delete_checkpoint(self, manager, mock_mcp):
        manager.save("v1", mock_mcp, message_count=3)
        manager.save("v2", mock_mcp, message_count=5)
        assert manager.count == 2
        assert manager.delete("v1") is True
        assert manager.count == 1
        assert manager.get("v1") is None
        assert manager.get("v2") is not None

    def test_delete_nonexistent(self, manager):
        assert manager.delete("missing") is False

    def test_clear(self, manager, mock_mcp):
        manager.save("v1", mock_mcp, message_count=3)
        manager.save("v2", mock_mcp, message_count=5)
        assert manager.count == 2
        manager.clear()
        assert manager.count == 0
        assert manager.list_all() == []

    def test_restore_truncates_conversation(self, manager, mock_mcp):
        manager.save("v1", mock_mcp, message_count=3)
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
            {"role": "user", "content": "msg3"},
        ]
        result = manager.restore("v1", mock_mcp, messages)
        assert result['success'] is True
        assert result['new_message_count'] == 3
        assert result['messages_truncated'] == 2
        assert result['checkpoint']['name'] == 'v1'

    def test_restore_performs_undos(self, manager, mock_mcp):
        """Restore should undo timeline entries added after the checkpoint."""
        manager.save("v1", mock_mcp, message_count=3)

        # Now mock a longer timeline (4 items = 2 undos needed from the 2 at save)
        def execute_after(name, params):
            if name == 'get_timeline':
                return {
                    'success': True,
                    'timeline': [
                        {'index': 0, 'name': 'Sketch1'},
                        {'index': 1, 'name': 'Extrusion1'},
                        {'index': 2, 'name': 'Fillet1'},
                        {'index': 3, 'name': 'Chamfer1'},
                    ],
                }
            elif name == 'undo':
                return {'success': True}
            return {'success': False}

        mock_mcp.execute_tool = MagicMock(side_effect=execute_after)
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(5)]
        result = manager.restore("v1", mock_mcp, messages)
        assert result['success'] is True
        assert result['undos_performed'] == 2

    def test_restore_nonexistent(self, manager, mock_mcp):
        messages = []
        result = manager.restore("missing", mock_mcp, messages)
        assert result['success'] is False
        assert 'not found' in result['error']

    def test_restore_removes_later_checkpoints(self, manager, mock_mcp):
        manager.save("v1", mock_mcp, message_count=3)
        manager.save("v2", mock_mcp, message_count=5)
        manager.save("v3", mock_mcp, message_count=8)
        assert manager.count == 3

        messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        result = manager.restore("v1", mock_mcp, messages)
        assert result['success'] is True
        # v2 and v3 should be removed
        assert manager.count == 1
        assert manager.get("v1") is not None
        assert manager.get("v2") is None
        assert manager.get("v3") is None

    def test_latest_checkpoint(self, manager, mock_mcp):
        assert manager.get_latest() is None
        manager.save("v1", mock_mcp, message_count=3)
        manager.save("v2", mock_mcp, message_count=5)
        latest = manager.get_latest()
        assert latest is not None
        assert latest.name == "v2"

    def test_save_with_tool_failures(self):
        """Checkpoint should still save even if F360 queries fail."""
        mgr = CheckpointManager()
        server = MagicMock()
        server.execute_tool = MagicMock(side_effect=Exception("connection lost"))

        cp = mgr.save("safe", server, message_count=2)
        assert cp.name == "safe"
        assert cp.timeline_position == 0
        assert cp.body_count == 0
        assert cp.message_index == 2


# ---------------------------------------------------------------------------
# TASK-173: Checkpoint timeout handling
# ---------------------------------------------------------------------------

class TestCheckpointTimeout:
    """TASK-173: Timeout and warning handling for checkpoint operations."""

    def test_default_timeout_values(self):
        """Default timeout and warning threshold should be set correctly."""
        mgr = CheckpointManager()
        assert mgr._timeout == 30.0
        assert mgr._warning_threshold == 5.0

    def test_custom_timeout_values(self):
        """Custom timeout and warning threshold should be accepted."""
        mgr = CheckpointManager(timeout=60.0, warning_threshold=10.0)
        assert mgr._timeout == 60.0
        assert mgr._warning_threshold == 10.0

    def test_set_warn_callback(self):
        """set_warn_callback should store the callback."""
        mgr = CheckpointManager()
        assert mgr._warn_callback is None

        warnings = []
        mgr.set_warn_callback(lambda msg: warnings.append(msg))
        assert mgr._warn_callback is not None

    def test_warn_callback_fires_after_threshold(self):
        """Warning callback should fire when operation exceeds threshold."""
        import time

        warnings = []
        # Use a very small threshold so the test doesn't take long
        mgr = CheckpointManager(timeout=30.0, warning_threshold=0.0)
        mgr.set_warn_callback(lambda msg: warnings.append(msg))

        # Mock server that takes a tiny amount of time
        server = MagicMock()

        def slow_execute(name, params):
            # Even a tiny delay will exceed threshold=0.0
            time.sleep(0.01)
            if name == 'get_timeline':
                return {'success': True, 'timeline': []}
            elif name == 'get_body_list':
                return {'success': True, 'count': 0}
            return {'success': False}

        server.execute_tool = MagicMock(side_effect=slow_execute)

        cp = mgr.save("warn_test", server, message_count=1)
        assert cp.name == "warn_test"
        # With threshold=0.0, at least one warning should have fired
        assert len(warnings) >= 1
        assert "taking longer than expected" in warnings[0]

    def test_timeout_handling_does_not_crash(self):
        """Even if queries timeout, save should still return a checkpoint."""
        import time

        warnings = []
        # Very low timeout
        mgr = CheckpointManager(timeout=0.0, warning_threshold=0.0)
        mgr.set_warn_callback(lambda msg: warnings.append(msg))

        server = MagicMock()
        server.execute_tool = MagicMock(side_effect=Exception("timed out"))

        # Should not raise
        cp = mgr.save("timeout_test", server, message_count=3)
        assert cp.name == "timeout_test"
        assert cp.timeline_position == 0
        assert cp.body_count == 0
        assert cp.message_index == 3

    def test_no_warn_callback_does_not_crash(self):
        """When no warn callback is set, warnings should be silently skipped."""
        import time

        mgr = CheckpointManager(timeout=30.0, warning_threshold=0.0)
        # Do NOT set a warn callback

        server = MagicMock()

        def slow_execute(name, params):
            time.sleep(0.01)
            if name == 'get_timeline':
                return {'success': True, 'timeline': [{'index': 0}]}
            elif name == 'get_body_list':
                return {'success': True, 'count': 1}
            return {'success': False}

        server.execute_tool = MagicMock(side_effect=slow_execute)

        # Should not raise even without callback
        cp = mgr.save("no_callback", server, message_count=1)
        assert cp.name == "no_callback"

    def test_enforced_timeout_raises(self):
        """TASK-194: MCP server calls that exceed timeout must raise TimeoutError."""
        import time

        mgr = CheckpointManager(timeout=0.1, warning_threshold=0.05)

        server = MagicMock()

        def hanging_execute(name, params):
            time.sleep(5)  # simulate a hang far beyond the 0.1s timeout
            return {'success': True, 'timeline': []}

        server.execute_tool = MagicMock(side_effect=hanging_execute)

        # save() catches the TimeoutError internally and produces a partial
        # checkpoint, so it should not propagate.  But _call_with_timeout
        # itself should raise TimeoutError.
        with pytest.raises(TimeoutError, match="timed out after"):
            mgr._call_with_timeout(server.execute_tool, 'get_timeline', {})

    def test_save_with_hanging_server_returns_partial_checkpoint(self):
        """TASK-194: save() with a hanging server returns a checkpoint with defaults."""
        import time

        mgr = CheckpointManager(timeout=0.2, warning_threshold=0.05)
        warnings = []
        mgr.set_warn_callback(lambda msg: warnings.append(msg))

        server = MagicMock()

        def hanging_execute(name, params):
            time.sleep(5)
            return {'success': True, 'timeline': []}

        server.execute_tool = MagicMock(side_effect=hanging_execute)

        cp = mgr.save("hang_test", server, message_count=4)
        assert cp.name == "hang_test"
        assert cp.timeline_position == 0  # partial -- no data retrieved
        assert cp.body_count == 0
        assert cp.message_index == 4

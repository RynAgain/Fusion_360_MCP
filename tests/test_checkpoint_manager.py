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

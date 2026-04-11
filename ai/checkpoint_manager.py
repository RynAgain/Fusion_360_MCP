"""Design checkpoint system linking F360 timeline state to conversation state."""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class DesignCheckpoint:
    """A named restore point in the design process."""
    def __init__(self, name: str, timeline_position: int, body_count: int,
                 message_index: int, description: str = ""):
        self.name = name
        self.timeline_position = timeline_position
        self.body_count = body_count
        self.message_index = message_index  # index into conversation history
        self.description = description
        self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'timeline_position': self.timeline_position,
            'body_count': self.body_count,
            'message_index': self.message_index,
            'description': self.description,
            'created_at': self.created_at,
        }


class CheckpointManager:
    """Manages design checkpoints that link F360 state to conversation state."""

    def __init__(self):
        self._checkpoints: list[DesignCheckpoint] = []

    def save(self, name: str, mcp_server, message_count: int, description: str = "") -> DesignCheckpoint:
        """
        Save a checkpoint by recording the current F360 state.

        Args:
            name: Human-readable checkpoint name
            mcp_server: MCPServer instance to query current state
            message_count: Current length of conversation history
            description: Optional description of what was done
        """
        # Query current state from Fusion 360
        timeline_pos = 0
        body_count = 0

        try:
            timeline_result = mcp_server.execute_tool('get_timeline', {})
            if timeline_result.get('success'):
                timeline = timeline_result.get('timeline', [])
                timeline_pos = len(timeline)
        except Exception as e:
            logger.warning(f"Failed to get timeline for checkpoint: {e}")

        try:
            bodies_result = mcp_server.execute_tool('get_body_list', {})
            if bodies_result.get('success'):
                body_count = bodies_result.get('count', 0)
        except Exception as e:
            logger.warning(f"Failed to get body count for checkpoint: {e}")

        checkpoint = DesignCheckpoint(
            name=name,
            timeline_position=timeline_pos,
            body_count=body_count,
            message_index=message_count,
            description=description
        )

        self._checkpoints.append(checkpoint)
        logger.info(f"Checkpoint saved: '{name}' at timeline pos {timeline_pos}, {body_count} bodies")
        return checkpoint

    def restore(self, name: str, mcp_server, messages: list) -> dict:
        """
        Restore to a checkpoint by rolling back F360 timeline and truncating conversation.

        Returns:
            {
                'success': bool,
                'checkpoint': dict,
                'undos_performed': int,
                'messages_truncated': int,
                'new_message_count': int,
            }
        """
        checkpoint = self.get(name)
        if not checkpoint:
            return {'success': False, 'error': f'Checkpoint "{name}" not found'}

        # Determine how many undos needed
        current_timeline_pos = 0
        try:
            timeline_result = mcp_server.execute_tool('get_timeline', {})
            if timeline_result.get('success'):
                current_timeline_pos = len(timeline_result.get('timeline', []))
        except Exception:
            pass

        undos_needed = current_timeline_pos - checkpoint.timeline_position
        undos_performed = 0

        # Perform undos to roll back the F360 timeline
        if undos_needed > 0:
            for i in range(undos_needed):
                try:
                    result = mcp_server.execute_tool('undo', {})
                    if result.get('success'):
                        undos_performed += 1
                    else:
                        break
                except Exception:
                    break

        # Truncate conversation history
        old_count = len(messages)
        new_count = min(checkpoint.message_index, len(messages))
        messages_truncated = old_count - new_count

        # Remove checkpoints that were created after this one
        checkpoint_index = self._checkpoints.index(checkpoint)
        removed = self._checkpoints[checkpoint_index + 1:]
        self._checkpoints = self._checkpoints[:checkpoint_index + 1]

        logger.info(f"Restored checkpoint '{name}': {undos_performed} undos, "
                    f"{messages_truncated} messages truncated, "
                    f"{len(removed)} later checkpoints removed")

        return {
            'success': True,
            'checkpoint': checkpoint.to_dict(),
            'undos_performed': undos_performed,
            'messages_truncated': messages_truncated,
            'new_message_count': new_count,
        }

    def get(self, name: str) -> DesignCheckpoint:
        """Get a checkpoint by name."""
        for cp in self._checkpoints:
            if cp.name == name:
                return cp
        return None

    def list_all(self) -> list[dict]:
        """List all checkpoints."""
        return [cp.to_dict() for cp in self._checkpoints]

    def delete(self, name: str) -> bool:
        """Delete a checkpoint."""
        for i, cp in enumerate(self._checkpoints):
            if cp.name == name:
                self._checkpoints.pop(i)
                return True
        return False

    def clear(self):
        """Clear all checkpoints."""
        self._checkpoints.clear()

    @property
    def count(self) -> int:
        return len(self._checkpoints)

    def get_latest(self) -> DesignCheckpoint:
        """Get the most recent checkpoint."""
        return self._checkpoints[-1] if self._checkpoints else None

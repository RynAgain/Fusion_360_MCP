"""Design checkpoint system linking F360 timeline state to conversation state."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class DesignCheckpoint:
    """A named restore point in the design process."""
    name: str
    timeline_position: int
    body_count: int
    message_index: int  # index into conversation history
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

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
    """Manages design checkpoints that link F360 state to conversation state.

    Optionally integrates with :class:`~ai.git_design_manager.GitDesignManager`
    to also create git commits alongside design checkpoints.
    """

    def __init__(self, git_manager=None, timeout: float = 30.0, warning_threshold: float = 5.0):
        self._checkpoints: list[DesignCheckpoint] = []
        self._git_manager = git_manager
        self._timeout = timeout
        self._warning_threshold = warning_threshold
        self._warn_callback: Callable[[str], None] | None = None

    def set_warn_callback(self, callback: Callable[[str], None]) -> None:
        """Set a callback for checkpoint operation warnings.

        TASK-173: The callback is invoked when a checkpoint operation
        exceeds the warning threshold or times out.
        """
        self._warn_callback = callback

    def _call_with_timeout(self, fn, *args, **kwargs):
        """Execute *fn* with ``self._timeout`` second deadline.

        TASK-194: Wraps MCP server calls so that a hanging Fusion 360
        connection cannot block indefinitely.
        """
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn, *args, **kwargs)
            try:
                return future.result(timeout=self._timeout)
            except FuturesTimeoutError:
                raise TimeoutError(
                    f"Fusion 360 operation timed out after {self._timeout}s"
                )

    def save(self, name: str, mcp_server, message_count: int, description: str = "") -> DesignCheckpoint:
        """
        Save a checkpoint by recording the current F360 state.

        TASK-173: Includes timeout awareness and warning callbacks.
        If querying Fusion 360 state takes longer than ``_warning_threshold``,
        emits a warning via the warn callback.  If it exceeds ``_timeout``,
        saves a partial checkpoint with whatever state was retrieved.

        Args:
            name: Human-readable checkpoint name
            mcp_server: MCPServer instance to query current state
            message_count: Current length of conversation history
            description: Optional description of what was done
        """
        # TASK-173: Track timing for timeout/warning handling
        start_time = time.monotonic()
        warning_sent = False

        def _check_warning():
            nonlocal warning_sent
            elapsed = time.monotonic() - start_time
            if elapsed >= self._warning_threshold and not warning_sent:
                warning_sent = True
                if self._warn_callback:
                    self._warn_callback(
                        f"Checkpoint '{name}' is taking longer than expected "
                        f"({self._warning_threshold:.0f}s)..."
                    )

        # Query current state from Fusion 360
        timeline_pos = 0
        body_count = 0

        try:
            timeline_result = self._call_with_timeout(
                mcp_server.execute_tool, 'get_timeline', {}
            )
            if timeline_result.get('success'):
                timeline = timeline_result.get('timeline', [])
                timeline_pos = len(timeline)
            _check_warning()
        except TimeoutError:
            elapsed = time.monotonic() - start_time
            logger.warning("Checkpoint '%s' timeline query timed out after %.1fs", name, elapsed)
            if self._warn_callback:
                self._warn_callback(f"Checkpoint '{name}' timed out after {elapsed:.1f}s")
        except Exception as e:
            elapsed = time.monotonic() - start_time
            if elapsed >= self._timeout:
                logger.warning("Checkpoint '%s' timeline query timed out after %.1fs", name, elapsed)
                if self._warn_callback:
                    self._warn_callback(f"Checkpoint '{name}' timed out after {elapsed:.1f}s")
            else:
                logger.warning("Failed to get timeline for checkpoint: %s", e)

        try:
            _check_warning()
            bodies_result = self._call_with_timeout(
                mcp_server.execute_tool, 'get_body_list', {}
            )
            if bodies_result.get('success'):
                body_count = bodies_result.get('count', 0)
            _check_warning()
        except TimeoutError:
            elapsed = time.monotonic() - start_time
            logger.warning("Checkpoint '%s' body query timed out after %.1fs", name, elapsed)
            if self._warn_callback:
                self._warn_callback(f"Checkpoint '{name}' timed out after {elapsed:.1f}s")
        except Exception as e:
            elapsed = time.monotonic() - start_time
            if elapsed >= self._timeout:
                logger.warning("Checkpoint '%s' body query timed out after %.1fs", name, elapsed)
                if self._warn_callback:
                    self._warn_callback(f"Checkpoint '{name}' timed out after {elapsed:.1f}s")
            else:
                logger.warning("Failed to get body count for checkpoint: %s", e)

        checkpoint = DesignCheckpoint(
            name=name,
            timeline_position=timeline_pos,
            body_count=body_count,
            message_index=message_count,
            description=description
        )

        self._checkpoints.append(checkpoint)
        logger.info("Checkpoint saved: '%s' at timeline pos %d, %d bodies", name, timeline_pos, body_count)

        # Git integration: create a git commit for this checkpoint
        if self._git_manager is not None:
            try:
                self._git_manager.checkpoint(
                    f"Checkpoint: {name}" + (f" -- {description}" if description else ""),
                    state_data=checkpoint.to_dict(),
                )
            except Exception as exc:
                logger.warning("Git checkpoint failed for '%s': %s", name, exc)

        return checkpoint

    def restore(self, name: str, mcp_server, messages: list) -> dict:
        """
        Restore to a checkpoint by rolling back F360 timeline AND truncating
        the conversation history atomically (TASK-017).

        Both the destructive timeline rollback and the conversation
        truncation happen inside the same try/except so that if either
        fails the caller gets a clear error and knows the state may be
        inconsistent.

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

        undos_performed = 0
        old_count = len(messages)
        new_count = min(checkpoint.message_index, len(messages))

        try:
            # --- Phase 1: Timeline rollback ---
            current_timeline_pos = 0
            try:
                timeline_result = mcp_server.execute_tool('get_timeline', {})
                if timeline_result.get('success'):
                    current_timeline_pos = len(timeline_result.get('timeline', []))
            except Exception as exc:
                # TASK-025: Log instead of silently swallowing
                logger.warning(
                    "Checkpoint restore: failed to query timeline position: %s", exc,
                )

            undos_needed = current_timeline_pos - checkpoint.timeline_position

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

            # --- Phase 2: Conversation truncation (TASK-017 atomic) ---
            # Mutate the list in-place so the caller's reference is updated
            # within the same protected block as the timeline rollback.
            del messages[new_count:]

            messages_truncated = old_count - new_count

            # --- Phase 3: Clean up later checkpoints ---
            try:
                checkpoint_index = self._checkpoints.index(checkpoint)
            except ValueError:
                # Checkpoint was removed concurrently; skip cleanup
                checkpoint_index = len(self._checkpoints) - 1
            removed = self._checkpoints[checkpoint_index + 1:]
            self._checkpoints = self._checkpoints[:checkpoint_index + 1]

            logger.info(
                "Restored checkpoint '%s': %d undos, %d messages truncated, "
                "%d later checkpoints removed",
                name, undos_performed, messages_truncated, len(removed),
            )

            return {
                'success': True,
                'checkpoint': checkpoint.to_dict(),
                'undos_performed': undos_performed,
                'messages_truncated': messages_truncated,
                'new_message_count': new_count,
            }

        except Exception as exc:
            # TASK-017: If anything fails after partial rollback, log a
            # critical warning so the inconsistent state is visible.
            logger.critical(
                "Checkpoint restore FAILED after %d undos (state may be "
                "inconsistent): %s",
                undos_performed, exc,
            )
            return {
                'success': False,
                'error': (
                    f"Restore failed after {undos_performed} undo(s): {exc}. "
                    "Design state may be inconsistent -- consider using Fusion "
                    "360's Edit > Undo to manually recover."
                ),
                'undos_performed': undos_performed,
            }

    def get(self, name: str) -> Optional[DesignCheckpoint]:
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

    def get_latest(self) -> Optional[DesignCheckpoint]:
        """Get the most recent checkpoint."""
        return self._checkpoints[-1] if self._checkpoints else None

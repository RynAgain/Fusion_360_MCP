"""Message queue for mid-turn user input injection.

Inspired by Roo Code's MessageQueueService. Allows users to send
messages while the agent is running, which get injected at the next
safe point between tool calls.
"""
import threading
import logging
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class QueuedMessage:
    """A message queued for injection during an active turn."""
    text: str
    images: list = field(default_factory=list)


class MessageQueue:
    """Thread-safe message queue for mid-turn injection."""

    def __init__(self):
        self._queue: deque[QueuedMessage] = deque()
        self._lock = threading.Lock()

    def enqueue(self, text: str, images: list | None = None) -> None:
        """Add a message to the queue.

        Silently drops empty or whitespace-only messages to prevent
        the Anthropic API from rejecting turns with empty content blocks.
        """
        if not text or not text.strip():
            logger.debug("Skipping empty mid-turn message enqueue (would cause API 400)")
            return
        with self._lock:
            self._queue.append(QueuedMessage(text=text, images=images or []))
        logger.debug("Queued user message for mid-turn injection")

    def drain(self) -> list[QueuedMessage]:
        """Remove and return all queued messages."""
        with self._lock:
            messages = list(self._queue)
            self._queue.clear()
        return messages

    def has_messages(self) -> bool:
        """Check if there are queued messages without draining."""
        with self._lock:
            return len(self._queue) > 0

    def clear(self) -> None:
        """Clear all queued messages."""
        with self._lock:
            self._queue.clear()

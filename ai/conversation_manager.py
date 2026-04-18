"""
ai/conversation_manager.py
Conversation persistence manager.

Handles saving, loading, listing, and deleting conversations as JSON
files stored in the ``data/conversations/`` directory.  This is a pure
persistence layer -- the in-memory conversation state lives in
:class:`ai.claude_client.ClaudeClient`.
"""

import os
import json
import re
import uuid
import logging
from datetime import datetime, timezone

from ai.log_sanitizer import sanitize

logger = logging.getLogger(__name__)

# Directory for saved conversations
CONVERSATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "conversations"
)


# Security: strict UUID-v4 pattern to prevent path-traversal via conversation_id
_UUID_PATTERN = re.compile(
    r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
)


def _validate_conversation_id(conversation_id: str) -> None:
    """Raise ValueError if conversation_id is not a valid UUID hex string.

    Security: prevents path-traversal attacks where a crafted conversation_id
    like ``../../etc/passwd`` could read/write arbitrary files.
    """
    if not _UUID_PATTERN.match(conversation_id):
        raise ValueError(
            f"Invalid conversation_id: {conversation_id!r}. "
            "Must be a UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)."
        )


class ConversationManager:
    """Manages conversation persistence to/from JSON files on disk."""

    def __init__(self):
        os.makedirs(CONVERSATIONS_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, conversation_id: str, messages: list, title: str = None) -> dict:
        """
        Save a conversation to disk.

        Parameters:
            conversation_id: UUID string identifying the conversation.
                             If empty/None a new UUID is generated.
            messages:        The full message list (same format as the
                             Anthropic Messages API).
            title:           Optional human-readable title.  Auto-generated
                             from the first user message when omitted.

        Returns:
            Conversation metadata dict (no messages).
        """
        if not conversation_id:
            conversation_id = str(uuid.uuid4())

        # Security: validate conversation_id to prevent path traversal
        _validate_conversation_id(conversation_id)

        # Auto-generate title from first user message if not provided
        if not title:
            title = self._auto_title(messages)

        # TASK-026: Replace deprecated datetime.utcnow() with timezone-aware alternative
        now = datetime.now(timezone.utc).isoformat()
        message_count = len(messages)
        data = {
            "id": conversation_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "message_count": message_count,
            "messages": messages,
        }

        filepath = os.path.join(CONVERSATIONS_DIR, f"{conversation_id}.json")

        # If the file already exists, preserve the original created_at
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                data["created_at"] = existing.get("created_at", data["created_at"])
            except Exception:
                pass  # fall through with new created_at

        # TASK-091: Single-pass serialization + sanitization (no double round-trip)
        raw_json = json.dumps(data, indent=2, default=str)
        sanitized_json = sanitize(raw_json)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(sanitized_json)

        logger.info(
            "Saved conversation %s (%d messages)", conversation_id, message_count,
        )
        return self._metadata(data)

    def load(self, conversation_id: str) -> dict | None:
        """
        Load a conversation from disk (including messages).

        Returns:
            The full conversation dict, or ``None`` if not found.
        """
        # Security: validate conversation_id to prevent path traversal
        _validate_conversation_id(conversation_id)
        filepath = os.path.join(CONVERSATIONS_DIR, f"{conversation_id}.json")
        if not os.path.exists(filepath):
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_all(self) -> list[dict]:
        """
        List all saved conversations (metadata only, no messages).

        Results are sorted by ``updated_at`` descending (most recent first).

        # TODO: TASK-092 -- Consider a metadata index file for large conversation
        # collections.  Currently each file is fully parsed even though only
        # top-level metadata fields are returned.  For typical usage (< 100
        # conversations) this is acceptable.
        """
        conversations: list[dict] = []
        if not os.path.exists(CONVERSATIONS_DIR):
            return conversations

        for filename in os.listdir(CONVERSATIONS_DIR):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(CONVERSATIONS_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                conversations.append(self._metadata(data))
            except Exception as e:
                logger.error("Failed to read %s: %s", filename, e)

        # Sort by updated_at descending
        conversations.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return conversations

    def delete(self, conversation_id: str) -> bool:
        """
        Delete a conversation file from disk.

        Returns:
            ``True`` if the file existed and was removed, ``False`` otherwise.
        """
        # Security: validate conversation_id to prevent path traversal
        _validate_conversation_id(conversation_id)
        filepath = os.path.join(CONVERSATIONS_DIR, f"{conversation_id}.json")
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info("Deleted conversation %s", conversation_id)
            return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auto_title(self, messages: list) -> str:
        """Generate a title from the first user message content."""
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")

            # content may be a plain string or a list of content blocks
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = next(
                    (
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ),
                    "",
                )
            else:
                continue

            if not text:
                continue
            # Truncate to 80 chars
            return text[:77] + "..." if len(text) > 80 else text

        return "New conversation"

    @staticmethod
    def _metadata(data: dict) -> dict:
        """Extract metadata (no messages) from a conversation dict."""
        return {
            "id": data["id"],
            "title": data.get("title", "Untitled"),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "message_count": data.get("message_count", 0),
        }

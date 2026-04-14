"""
tests/test_conversation_manager.py
Unit tests for ai/conversation_manager.py -- conversation persistence.

All tests use pytest's ``tmp_path`` fixture and monkeypatch the module-level
CONVERSATIONS_DIR so no files are written to the real data/ directory.
"""

import json
import time
import pytest
from ai.conversation_manager import ConversationManager
import ai.conversation_manager as cm_module


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

# Valid UUID strings for use in tests (deterministic, not random)
UUID_1 = "00000000-0000-4000-a000-000000000001"
UUID_2 = "00000000-0000-4000-a000-000000000002"
UUID_TS = "00000000-0000-4000-a000-0000000000aa"
UUID_OLD = "00000000-0000-4000-a000-0000000000bb"
UUID_NEW = "00000000-0000-4000-a000-0000000000cc"
UUID_DEL = "00000000-0000-4000-a000-0000000000dd"
UUID_T1 = "00000000-0000-4000-a000-000000000011"
UUID_T2 = "00000000-0000-4000-a000-000000000022"
UUID_T3 = "00000000-0000-4000-a000-000000000033"
UUID_T4 = "00000000-0000-4000-a000-000000000044"
UUID_NONEXIST = "ffffffff-ffff-4fff-bfff-ffffffffffff"


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    """
    Return a ConversationManager that writes into a temporary directory.
    Monkey-patches CONVERSATIONS_DIR at the module level so the manager's
    __init__ creates dirs in tmp_path.
    """
    convos_dir = str(tmp_path / "conversations")
    monkeypatch.setattr(cm_module, "CONVERSATIONS_DIR", convos_dir)
    return ConversationManager()


SAMPLE_MESSAGES = [
    {"role": "user", "content": "Create a 5 cm cylinder"},
    {"role": "assistant", "content": [{"type": "text", "text": "I'll create that for you."}]},
]


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------

class TestSaveAndLoad:
    """Verify save-then-load round-trip preserves conversation data."""

    def test_basic_round_trip(self, mgr):
        meta = mgr.save(UUID_1, SAMPLE_MESSAGES, title="Test Conv")
        assert meta["id"] == UUID_1

        loaded = mgr.load(UUID_1)
        assert loaded is not None
        assert loaded["id"] == UUID_1
        assert loaded["title"] == "Test Conv"
        assert len(loaded["messages"]) == 2

    def test_load_nonexistent_returns_none(self, mgr):
        assert mgr.load(UUID_NONEXIST) is None

    def test_save_generates_id_when_empty(self, mgr):
        meta = mgr.save("", SAMPLE_MESSAGES, title="Auto ID")
        assert meta["id"]  # non-empty
        assert len(meta["id"]) > 0

    def test_save_preserves_created_at(self, mgr):
        """Saving twice should keep the original created_at timestamp."""
        mgr.save(UUID_TS, SAMPLE_MESSAGES, title="Timestamps")
        first = mgr.load(UUID_TS)
        original_created = first["created_at"]

        # Small delay to ensure updated_at differs
        time.sleep(0.01)
        mgr.save(UUID_TS, SAMPLE_MESSAGES + [{"role": "user", "content": "more"}], title="Timestamps v2")
        second = mgr.load(UUID_TS)

        assert second["created_at"] == original_created
        assert second["updated_at"] >= original_created


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------

class TestListAll:
    """Verify listing and sorting of saved conversations."""

    def test_empty_list(self, mgr):
        assert mgr.list_all() == []

    def test_list_returns_metadata(self, mgr):
        mgr.save(UUID_1, SAMPLE_MESSAGES, title="First")
        mgr.save(UUID_2, SAMPLE_MESSAGES, title="Second")
        items = mgr.list_all()
        assert len(items) == 2
        for item in items:
            assert "messages" not in item  # metadata only
            assert "id" in item
            assert "title" in item

    def test_list_sorted_by_updated_at_desc(self, mgr):
        mgr.save(UUID_OLD, SAMPLE_MESSAGES, title="Old")
        time.sleep(0.01)
        mgr.save(UUID_NEW, SAMPLE_MESSAGES, title="New")
        items = mgr.list_all()
        assert items[0]["id"] == UUID_NEW
        assert items[1]["id"] == UUID_OLD


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

class TestDelete:
    """Verify conversation deletion."""

    def test_delete_existing(self, mgr):
        mgr.save(UUID_DEL, SAMPLE_MESSAGES)
        assert mgr.delete(UUID_DEL) is True
        assert mgr.load(UUID_DEL) is None

    def test_delete_nonexistent(self, mgr):
        assert mgr.delete(UUID_NONEXIST) is False


# ---------------------------------------------------------------------------
# auto-title generation
# ---------------------------------------------------------------------------

class TestAutoTitle:
    """Verify automatic title extraction from user messages."""

    def test_title_from_first_user_message(self, mgr):
        msgs = [{"role": "user", "content": "Design a bracket"}]
        meta = mgr.save(UUID_T1, msgs)
        assert meta["title"] == "Design a bracket"

    def test_title_truncation_at_80_chars(self, mgr):
        long_text = "A" * 200
        msgs = [{"role": "user", "content": long_text}]
        meta = mgr.save(UUID_T2, msgs)
        assert len(meta["title"]) <= 80

    def test_title_from_content_blocks(self, mgr):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Make a gear with 20 teeth"},
                ],
            }
        ]
        meta = mgr.save(UUID_T3, msgs)
        assert "gear" in meta["title"].lower()

    def test_default_title_when_no_user_message(self, mgr):
        msgs = [{"role": "assistant", "content": "Hello"}]
        meta = mgr.save(UUID_T4, msgs)
        assert meta["title"] == "New conversation"


# ---------------------------------------------------------------------------
# Security: conversation_id validation
# ---------------------------------------------------------------------------

class TestConversationIdValidation:
    """Verify that non-UUID conversation IDs are rejected."""

    def test_save_rejects_path_traversal(self, mgr):
        with pytest.raises(ValueError, match="Invalid conversation_id"):
            mgr.save("../../etc/passwd", SAMPLE_MESSAGES)

    def test_load_rejects_path_traversal(self, mgr):
        with pytest.raises(ValueError, match="Invalid conversation_id"):
            mgr.load("../../../secret")

    def test_delete_rejects_path_traversal(self, mgr):
        with pytest.raises(ValueError, match="Invalid conversation_id"):
            mgr.delete("del-me")

    def test_save_rejects_non_uuid(self, mgr):
        with pytest.raises(ValueError, match="Invalid conversation_id"):
            mgr.save("not-a-uuid", SAMPLE_MESSAGES)

    def test_valid_uuid_accepted(self, mgr):
        # Should not raise
        meta = mgr.save("abcdef01-2345-6789-abcd-ef0123456789", SAMPLE_MESSAGES)
        assert meta["id"] == "abcdef01-2345-6789-abcd-ef0123456789"

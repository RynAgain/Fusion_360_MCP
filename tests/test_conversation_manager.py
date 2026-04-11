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
# Fixtures
# ---------------------------------------------------------------------------

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
        meta = mgr.save("conv-001", SAMPLE_MESSAGES, title="Test Conv")
        assert meta["id"] == "conv-001"

        loaded = mgr.load("conv-001")
        assert loaded is not None
        assert loaded["id"] == "conv-001"
        assert loaded["title"] == "Test Conv"
        assert len(loaded["messages"]) == 2

    def test_load_nonexistent_returns_none(self, mgr):
        assert mgr.load("does-not-exist") is None

    def test_save_generates_id_when_empty(self, mgr):
        meta = mgr.save("", SAMPLE_MESSAGES, title="Auto ID")
        assert meta["id"]  # non-empty
        assert len(meta["id"]) > 0

    def test_save_preserves_created_at(self, mgr):
        """Saving twice should keep the original created_at timestamp."""
        mgr.save("conv-ts", SAMPLE_MESSAGES, title="Timestamps")
        first = mgr.load("conv-ts")
        original_created = first["created_at"]

        # Small delay to ensure updated_at differs
        time.sleep(0.01)
        mgr.save("conv-ts", SAMPLE_MESSAGES + [{"role": "user", "content": "more"}], title="Timestamps v2")
        second = mgr.load("conv-ts")

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
        mgr.save("c1", SAMPLE_MESSAGES, title="First")
        mgr.save("c2", SAMPLE_MESSAGES, title="Second")
        items = mgr.list_all()
        assert len(items) == 2
        for item in items:
            assert "messages" not in item  # metadata only
            assert "id" in item
            assert "title" in item

    def test_list_sorted_by_updated_at_desc(self, mgr):
        mgr.save("old", SAMPLE_MESSAGES, title="Old")
        time.sleep(0.01)
        mgr.save("new", SAMPLE_MESSAGES, title="New")
        items = mgr.list_all()
        assert items[0]["id"] == "new"
        assert items[1]["id"] == "old"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

class TestDelete:
    """Verify conversation deletion."""

    def test_delete_existing(self, mgr):
        mgr.save("del-me", SAMPLE_MESSAGES)
        assert mgr.delete("del-me") is True
        assert mgr.load("del-me") is None

    def test_delete_nonexistent(self, mgr):
        assert mgr.delete("nope") is False


# ---------------------------------------------------------------------------
# auto-title generation
# ---------------------------------------------------------------------------

class TestAutoTitle:
    """Verify automatic title extraction from user messages."""

    def test_title_from_first_user_message(self, mgr):
        msgs = [{"role": "user", "content": "Design a bracket"}]
        meta = mgr.save("t1", msgs)
        assert meta["title"] == "Design a bracket"

    def test_title_truncation_at_80_chars(self, mgr):
        long_text = "A" * 200
        msgs = [{"role": "user", "content": long_text}]
        meta = mgr.save("t2", msgs)
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
        meta = mgr.save("t3", msgs)
        assert "gear" in meta["title"].lower()

    def test_default_title_when_no_user_message(self, mgr):
        msgs = [{"role": "assistant", "content": "Hello"}]
        meta = mgr.save("t4", msgs)
        assert meta["title"] == "New conversation"

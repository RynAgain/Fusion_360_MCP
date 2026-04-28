"""
tests/test_conftest_isolation.py
Regression tests verifying that the autouse fixture in conftest.py
properly redirects conversation and session-report persistence to
temporary directories so no files leak into the real data/ tree.
"""

import os
import json
import pytest

from ai.conversation_manager import ConversationManager
import ai.conversation_manager as cm_mod
import ai.session_report as sr_mod
from ai.session_report import SessionFailureReport


# Valid UUID for tests
UUID_ISOLATION = "00000000-0000-4000-a000-00000000cafe"

# Absolute path to the *real* data/conversations directory (before patching)
_REAL_CONVERSATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "conversations",
)


class TestConversationManagerIsolation:
    """Verify ConversationManager writes go to tmp_path, not data/."""

    def test_save_writes_to_temp_dir_not_real_dir(self):
        """Saving a conversation must NOT create a file in data/conversations/."""
        mgr = ConversationManager()
        mgr.save(UUID_ISOLATION, [{"role": "user", "content": "test"}], title="Isolation test")

        # File must NOT exist in the real directory
        real_path = os.path.join(_REAL_CONVERSATIONS_DIR, f"{UUID_ISOLATION}.json")
        assert not os.path.exists(real_path), (
            f"ConversationManager wrote to real data/ directory: {real_path}"
        )

        # File MUST exist in the patched (temp) directory
        patched_path = os.path.join(cm_mod.CONVERSATIONS_DIR, f"{UUID_ISOLATION}.json")
        assert os.path.exists(patched_path), (
            f"ConversationManager did not write to patched directory: {patched_path}"
        )

    def test_conversations_dir_is_patched(self):
        """The module-level CONVERSATIONS_DIR should point to a temp directory."""
        assert cm_mod.CONVERSATIONS_DIR != _REAL_CONVERSATIONS_DIR, (
            "CONVERSATIONS_DIR was not patched by conftest.py autouse fixture"
        )


class TestSessionReportIsolation:
    """Verify SessionFailureReport.save() writes to tmp_path, not data/."""

    def test_save_writes_to_temp_dir_not_real_dir(self):
        """Saving a failure report must NOT create a file in data/conversations/."""
        report = SessionFailureReport()
        report.set_termination_reason("iteration_limit")
        report.collect()  # minimal collection

        filepath = report.save(UUID_ISOLATION)

        # The returned path must be in the patched directory
        assert filepath is not None
        assert filepath.startswith(sr_mod._CONVERSATIONS_DIR), (
            f"Report saved outside patched dir: {filepath}"
        )

        # File must NOT exist in the real directory
        real_path = os.path.join(
            _REAL_CONVERSATIONS_DIR,
            f"{UUID_ISOLATION}_failure_report.json",
        )
        assert not os.path.exists(real_path), (
            f"SessionFailureReport wrote to real data/ directory: {real_path}"
        )

    def test_conversations_dir_is_patched(self):
        """The module-level _CONVERSATIONS_DIR should point to a temp directory."""
        assert sr_mod._CONVERSATIONS_DIR != _REAL_CONVERSATIONS_DIR, (
            "_CONVERSATIONS_DIR was not patched by conftest.py autouse fixture"
        )


class TestIsolationDoesNotBreakFunctionality:
    """Verify that patching does not break normal save/load round-trips."""

    def test_round_trip_still_works(self):
        """Save and load via ConversationManager still works in isolation."""
        mgr = ConversationManager()
        msgs = [{"role": "user", "content": "round-trip test"}]
        meta = mgr.save(UUID_ISOLATION, msgs, title="Round trip")
        assert meta["id"] == UUID_ISOLATION

        loaded = mgr.load(UUID_ISOLATION)
        assert loaded is not None
        assert loaded["title"] == "Round trip"
        assert len(loaded["messages"]) == 1

    def test_list_all_sees_saved_conversations(self):
        """list_all should see conversations saved during the test."""
        mgr = ConversationManager()
        mgr.save(UUID_ISOLATION, [{"role": "user", "content": "hi"}], title="Listed")
        items = mgr.list_all()
        ids = [i["id"] for i in items]
        assert UUID_ISOLATION in ids

    def test_failure_report_content_correct(self):
        """Failure report file contains valid JSON with expected keys."""
        report = SessionFailureReport()
        report.set_termination_reason("force_stop")
        report.collect()
        filepath = report.save(UUID_ISOLATION)

        assert filepath is not None
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["report_type"] == "session_failure_report"
        assert data["termination_reason"] == "force_stop"
        assert data["conversation_id"] == UUID_ISOLATION

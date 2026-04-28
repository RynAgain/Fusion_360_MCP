"""
tests/conftest.py
Global pytest fixtures for the Artifex360 test suite.

Provides autouse fixtures that redirect all filesystem-backed persistence
modules (conversation manager, session reports) to temporary directories
so that tests never write to the real ``data/`` directory tree.

This prevents:
- Failure report JSON files accumulating in ``data/conversations/``
- Conversation files leaking from agent-loop / web-route tests
"""

import os
import pytest


@pytest.fixture(autouse=True)
def _isolate_conversation_dirs(tmp_path, monkeypatch):
    """Redirect both conversation persistence paths to a temp directory.

    Patches:
    - ``ai.conversation_manager.CONVERSATIONS_DIR``
      (used by ConversationManager for save/load/list/delete)
    - ``ai.session_report._CONVERSATIONS_DIR``
      (used by SessionFailureReport.save())

    The fixture is **autouse** so every test gets isolation automatically.
    Individual tests that already monkeypatch these values (e.g.
    ``test_conversation_manager.py``) will simply overwrite the autouse
    patch -- last monkeypatch wins within a test scope.
    """
    convos_dir = str(tmp_path / "conversations")
    os.makedirs(convos_dir, exist_ok=True)

    import ai.conversation_manager as cm_mod
    import ai.session_report as sr_mod

    monkeypatch.setattr(cm_mod, "CONVERSATIONS_DIR", convos_dir)
    monkeypatch.setattr(sr_mod, "_CONVERSATIONS_DIR", convos_dir)

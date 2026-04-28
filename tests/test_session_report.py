"""
tests/test_session_report.py
TASK-238: Tests for post-session failure analysis report.
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from ai.session_report import SessionFailureReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_progress_tracker(productive=5, thrashing=3, neutral=2, restart=1):
    """Create a mock ProgressTracker with configurable stats."""
    total = productive + thrashing + neutral
    denom = productive + thrashing
    ratio = thrashing / denom if denom > 0 else 0.0
    mock = MagicMock()
    mock.to_dict.return_value = {
        "productive_count": productive,
        "thrashing_count": thrashing,
        "neutral_count": neutral,
        "restart_count": restart,
        "total_calls": total,
        "thrashing_ratio": ratio,
    }
    return mock


def _make_script_error_tracker(unique=2, total=5, blocked=1):
    """Create a mock ScriptErrorTracker with configurable stats.

    Mirrors the real ScriptErrorTracker.get_stats() return format:
    ``tracked_signatures``, ``total_errors``, ``signatures`` dict.
    The ``block_threshold`` attribute controls when a signature counts
    as blocked (default 3, matching ScriptErrorTracker).
    """
    mock = MagicMock()
    mock.block_threshold = 3
    mock.get_stats.return_value = {
        "tracked_signatures": unique,
        "total_errors": total,
        "signatures": {
            "AttributeError:areaProperties": 3,
            "RuntimeError:extrude": 2,
        },
    }
    return mock


def _make_rebuild_detector(count=0):
    """Create a mock RebuildLoopDetector."""
    mock = MagicMock()
    mock.count = count
    return mock


def _make_mcp_server(blocklisted=None):
    """Create a mock MCPServer."""
    mock = MagicMock()
    mock.blocklisted_tools = set(blocklisted or [])
    return mock


# ---------------------------------------------------------------------------
# Report generation tests
# ---------------------------------------------------------------------------

class TestSessionFailureReportGeneration:
    """Tests for generating reports with all data sources."""

    def test_report_with_all_data_sources(self):
        """Report with all tracker subsystems available."""
        report = SessionFailureReport()
        report.set_termination_reason("iteration_limit")
        report.collect(
            progress_tracker=_make_progress_tracker(),
            script_error_tracker=_make_script_error_tracker(),
            rebuild_loop_detector=_make_rebuild_detector(count=2),
            mcp_server=_make_mcp_server(blocklisted=["edit_feature", "suppress_feature"]),
            context_pressure_triggered=True,
        )

        data = report.to_dict()
        assert data["report_type"] == "session_failure_report"
        assert data["termination_reason"] == "iteration_limit"
        assert data["error_summary"]["unique_errors"] == 2
        assert data["error_summary"]["total_script_errors"] == 5
        assert data["tool_usage_stats"]["productive_count"] == 5
        assert data["tool_usage_stats"]["thrashing_ratio"] == pytest.approx(0.375)
        assert data["rebuild_count"] == 2
        assert sorted(data["blocklisted_tools"]) == ["edit_feature", "suppress_feature"]
        assert data["context_pressure_triggered"] is True
        assert "generated_at" in data

    def test_report_with_partial_data_no_script_tracker(self):
        """Report with script_error_tracker unavailable."""
        report = SessionFailureReport()
        report.set_termination_reason("iteration_limit")
        report.collect(
            progress_tracker=_make_progress_tracker(),
            script_error_tracker=None,
            rebuild_loop_detector=_make_rebuild_detector(),
            mcp_server=_make_mcp_server(),
        )

        data = report.to_dict()
        assert data["error_summary"] == {}
        assert data["tool_usage_stats"]["productive_count"] == 5

    def test_report_with_partial_data_no_progress_tracker(self):
        """Report with progress_tracker unavailable."""
        report = SessionFailureReport()
        report.set_termination_reason("force_stop")
        report.collect(
            progress_tracker=None,
            script_error_tracker=_make_script_error_tracker(),
        )

        data = report.to_dict()
        assert data["tool_usage_stats"] == {}
        assert data["error_summary"]["unique_errors"] == 2

    def test_report_with_no_data_sources(self):
        """Report with all trackers unavailable."""
        report = SessionFailureReport()
        report.set_termination_reason("error")
        report.collect()

        data = report.to_dict()
        assert data["termination_reason"] == "error"
        assert data["error_summary"] == {}
        assert data["tool_usage_stats"] == {}
        assert data["rebuild_count"] == 0
        assert data["blocklisted_tools"] == []
        assert data["context_pressure_triggered"] is False

    def test_report_with_failing_tracker(self):
        """Report handles gracefully when a tracker raises an exception."""
        bad_tracker = MagicMock()
        bad_tracker.to_dict.side_effect = RuntimeError("tracker broken")

        report = SessionFailureReport()
        report.set_termination_reason("iteration_limit")
        report.collect(progress_tracker=bad_tracker)

        data = report.to_dict()
        assert "collection_error" in data["tool_usage_stats"]


# ---------------------------------------------------------------------------
# should_generate tests
# ---------------------------------------------------------------------------

class TestShouldGenerate:
    """Tests for the should_generate decision logic."""

    def test_iteration_limit_always_generates(self):
        report = SessionFailureReport()
        report.set_termination_reason("iteration_limit")
        report.collect()
        assert report.should_generate() is True

    def test_empty_responses_always_generates(self):
        report = SessionFailureReport()
        report.set_termination_reason("empty_responses")
        report.collect()
        assert report.should_generate() is True

    def test_force_stop_always_generates(self):
        report = SessionFailureReport()
        report.set_termination_reason("force_stop")
        report.collect()
        assert report.should_generate() is True

    def test_error_always_generates(self):
        report = SessionFailureReport()
        report.set_termination_reason("error")
        report.collect()
        assert report.should_generate() is True

    def test_normal_session_no_report(self):
        """Normal session without failures should not generate a report."""
        report = SessionFailureReport()
        report.set_termination_reason("normal")
        report.collect(
            progress_tracker=_make_progress_tracker(productive=10, thrashing=1),
            rebuild_loop_detector=_make_rebuild_detector(count=0),
            mcp_server=_make_mcp_server(blocklisted=[]),
        )
        assert report.should_generate() is False

    def test_high_rebuild_count_generates(self):
        """rebuild_count > 1 triggers report even for normal termination."""
        report = SessionFailureReport()
        report.set_termination_reason("normal")
        report.collect(
            rebuild_loop_detector=_make_rebuild_detector(count=3),
        )
        assert report.should_generate() is True

    def test_high_thrashing_ratio_generates(self):
        """thrashing_ratio > 0.5 triggers report even for normal termination."""
        report = SessionFailureReport()
        report.set_termination_reason("normal")
        report.collect(
            progress_tracker=_make_progress_tracker(productive=2, thrashing=8),
        )
        assert report.should_generate() is True

    def test_blocklisted_tools_generates(self):
        """Blocklisted tools trigger report even for normal termination."""
        report = SessionFailureReport()
        report.set_termination_reason("normal")
        report.collect(
            mcp_server=_make_mcp_server(blocklisted=["edit_feature"]),
        )
        assert report.should_generate() is True

    def test_user_cancel_no_issues_no_report(self):
        """User cancel without significant issues should not generate."""
        report = SessionFailureReport()
        report.set_termination_reason("user_cancel")
        report.collect(
            progress_tracker=_make_progress_tracker(productive=5, thrashing=1),
            rebuild_loop_detector=_make_rebuild_detector(count=0),
            mcp_server=_make_mcp_server(blocklisted=[]),
        )
        assert report.should_generate() is False


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------

class TestReportSave:
    """Tests for saving the report to disk."""

    def test_save_creates_file(self, tmp_path):
        """Report is saved to the correct path."""
        report = SessionFailureReport()
        report.set_termination_reason("iteration_limit")
        report.collect()

        with patch("ai.session_report._CONVERSATIONS_DIR", str(tmp_path)):
            filepath = report.save("test-conv-id-1234")

        assert filepath is not None
        assert filepath.endswith("_failure_report.json")
        assert os.path.exists(filepath)

        with open(filepath) as f:
            data = json.load(f)
        assert data["conversation_id"] == "test-conv-id-1234"
        assert data["termination_reason"] == "iteration_limit"

    def test_save_handles_missing_directory(self, tmp_path):
        """Report creates the directory if it does not exist."""
        new_dir = str(tmp_path / "new_subdir" / "conversations")
        report = SessionFailureReport()
        report.set_termination_reason("error")
        report.collect()

        with patch("ai.session_report._CONVERSATIONS_DIR", new_dir):
            filepath = report.save("conv-abc")

        assert filepath is not None
        assert os.path.exists(filepath)

    def test_save_returns_none_on_write_failure(self):
        """Report returns None when saving fails."""
        report = SessionFailureReport()
        report.set_termination_reason("error")
        report.collect()

        with patch("ai.session_report._CONVERSATIONS_DIR", "/nonexistent/impossible/path/\0bad"):
            filepath = report.save("conv-bad")

        assert filepath is None


# ---------------------------------------------------------------------------
# Termination reason tracking
# ---------------------------------------------------------------------------

class TestTerminationReason:
    """Tests for termination reason setting."""

    def test_default_reason_is_unknown(self):
        report = SessionFailureReport()
        data = report.to_dict()
        assert data["termination_reason"] == "unknown"

    def test_set_reason_persists(self):
        report = SessionFailureReport()
        report.set_termination_reason("iteration_limit")
        data = report.to_dict()
        assert data["termination_reason"] == "iteration_limit"

    def test_set_reason_can_be_overwritten(self):
        report = SessionFailureReport()
        report.set_termination_reason("iteration_limit")
        report.set_termination_reason("user_cancel")
        data = report.to_dict()
        assert data["termination_reason"] == "user_cancel"

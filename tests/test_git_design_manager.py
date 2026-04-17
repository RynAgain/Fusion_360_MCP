"""
tests/test_git_design_manager.py
Comprehensive tests for ai/git_design_manager.py -- git-based design state
management using branches as a state machine for Fusion 360 design iterations.

All git subprocess calls are mocked -- no actual git commands are executed.
"""

import json
import os
import textwrap

import pytest
from unittest.mock import MagicMock, call, mock_open, patch

from ai.git_design_manager import GitDesignManager, _TSV_HEADER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_completed_process(stdout="", stderr="", returncode=0):
    """Build a fake subprocess.CompletedProcess."""
    cp = MagicMock()
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


def _mock_run_factory(responses=None):
    """Return a side_effect function that maps git sub-commands to responses.

    *responses* is a dict mapping a substring of the command list to a
    CompletedProcess.  If the key ``"*"`` is present it serves as the
    default.
    """
    responses = responses or {}

    def _side_effect(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        for key, cp in responses.items():
            if key != "*" and key in cmd_str:
                return cp
        # Default
        if "*" in responses:
            return responses["*"]
        return _make_completed_process()

    return _side_effect


# ---------------------------------------------------------------------------
# TestGitDesignManager -- Initialization
# ---------------------------------------------------------------------------

class TestGitDesignManagerInit:
    """Tests for GitDesignManager.__init__."""

    @patch("subprocess.run")
    def test_valid_repo(self, mock_run):
        """Initializing with a valid git repo should succeed."""
        mock_run.return_value = _make_completed_process(stdout="true\n", returncode=0)
        mgr = GitDesignManager(repo_path="/fake/repo", branch_prefix="design")
        assert mgr._branch_prefix == "design"
        assert mgr._repo_path == os.path.abspath("/fake/repo")

    @patch("subprocess.run")
    def test_invalid_repo(self, mock_run):
        """Initializing with a non-git directory should raise ValueError."""
        mock_run.return_value = _make_completed_process(
            stderr="fatal: not a git repository", returncode=128,
        )
        with pytest.raises(ValueError, match="not a git repository"):
            GitDesignManager(repo_path="/not/a/repo")

    @patch("subprocess.run")
    def test_default_branch_prefix(self, mock_run):
        """Default branch_prefix should be 'design'."""
        mock_run.return_value = _make_completed_process(stdout="true\n", returncode=0)
        mgr = GitDesignManager(repo_path=".")
        assert mgr._branch_prefix == "design"


# ---------------------------------------------------------------------------
# TestGitDesignManager -- start_iteration
# ---------------------------------------------------------------------------

class TestStartIteration:
    """Tests for GitDesignManager.start_iteration."""

    @patch("subprocess.run")
    def test_new_branch(self, mock_run):
        """Creating a new design iteration should create a new branch."""
        responses = {
            "rev-parse --is-inside-work-tree": _make_completed_process("true\n"),
            "show-ref --verify": _make_completed_process(returncode=1),  # branch doesn't exist
            "checkout -b": _make_completed_process(),
        }
        mock_run.side_effect = _mock_run_factory(responses)

        mgr = GitDesignManager(repo_path="/fake/repo")
        branch = mgr.start_iteration("wall_thickness_v2")
        assert branch == "design/wall_thickness_v2"

    @patch("subprocess.run")
    def test_existing_branch(self, mock_run):
        """If branch already exists, should switch to it without creating."""
        responses = {
            "rev-parse --is-inside-work-tree": _make_completed_process("true\n"),
            "show-ref --verify": _make_completed_process(returncode=0),  # branch exists
            "checkout design/existing": _make_completed_process(),
        }
        mock_run.side_effect = _mock_run_factory(responses)

        mgr = GitDesignManager(repo_path="/fake/repo")
        branch = mgr.start_iteration("existing")
        assert branch == "design/existing"

    @patch("subprocess.run")
    def test_custom_prefix(self, mock_run):
        """Custom branch prefix should be used in branch names."""
        responses = {
            "rev-parse --is-inside-work-tree": _make_completed_process("true\n"),
            "show-ref --verify": _make_completed_process(returncode=1),
            "checkout -b": _make_completed_process(),
        }
        mock_run.side_effect = _mock_run_factory(responses)

        mgr = GitDesignManager(repo_path="/fake/repo", branch_prefix="iter")
        branch = mgr.start_iteration("test")
        assert branch == "iter/test"


# ---------------------------------------------------------------------------
# TestGitDesignManager -- checkpoint
# ---------------------------------------------------------------------------

class TestCheckpoint:
    """Tests for GitDesignManager.checkpoint."""

    @patch("subprocess.run")
    def test_checkpoint_without_state_data(self, mock_run):
        """Checkpoint without state_data should commit with message."""
        responses = {
            "rev-parse --is-inside-work-tree": _make_completed_process("true\n"),
            "add -A": _make_completed_process(),
            "commit": _make_completed_process(),
            "rev-parse --short HEAD": _make_completed_process("abc1234\n"),
            "*": _make_completed_process(),
        }
        mock_run.side_effect = _mock_run_factory(responses)

        mgr = GitDesignManager(repo_path="/fake/repo")
        commit = mgr.checkpoint("Increased wall thickness")
        assert commit == "abc1234"

    @patch("subprocess.run")
    def test_checkpoint_with_state_data(self, mock_run):
        """Checkpoint with state_data should write JSON and commit."""
        responses = {
            "rev-parse --is-inside-work-tree": _make_completed_process("true\n"),
            "rev-parse --abbrev-ref HEAD": _make_completed_process("design/bracket\n"),
            "rev-parse --short HEAD": _make_completed_process("def5678\n"),
            "*": _make_completed_process(),
        }
        mock_run.side_effect = _mock_run_factory(responses)

        state = {"bodies": [{"name": "Body1"}], "mass": 0.5}

        with patch("builtins.open", mock_open()) as m_open, \
             patch("os.makedirs") as m_makedirs, \
             patch("os.path.exists", return_value=False):
            mgr = GitDesignManager(repo_path="/fake/repo")
            commit = mgr.checkpoint("Added bracket", state_data=state)

        assert commit == "def5678"

    @patch("subprocess.run")
    def test_checkpoint_allow_empty(self, mock_run):
        """Checkpoint should use --allow-empty to handle no-change commits."""
        calls_made = []

        def capture_calls(cmd, **kwargs):
            calls_made.append(cmd)
            if "rev-parse" in cmd and "--is-inside-work-tree" in cmd:
                return _make_completed_process("true\n")
            if "rev-parse" in cmd and "--short" in cmd:
                return _make_completed_process("aaa1111\n")
            return _make_completed_process()

        mock_run.side_effect = capture_calls

        mgr = GitDesignManager(repo_path="/fake/repo")
        mgr.checkpoint("empty checkpoint")

        # Verify --allow-empty was passed
        commit_calls = [c for c in calls_made if "commit" in c]
        assert any("--allow-empty" in c for c in commit_calls)


# ---------------------------------------------------------------------------
# TestGitDesignManager -- accept_iteration
# ---------------------------------------------------------------------------

class TestAcceptIteration:
    """Tests for GitDesignManager.accept_iteration."""

    @patch("subprocess.run")
    def test_accept_without_metrics(self, mock_run):
        """Accept without metrics should log to TSV."""
        responses = {
            "rev-parse --is-inside-work-tree": _make_completed_process("true\n"),
            "rev-parse --short HEAD": _make_completed_process("abc1234\n"),
            "log -1 --format=%s": _make_completed_process("Increased wall thickness\n"),
            "*": _make_completed_process(),
        }
        mock_run.side_effect = _mock_run_factory(responses)

        with patch("builtins.open", mock_open()) as m_open, \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True):
            mgr = GitDesignManager(repo_path="/fake/repo")
            commit = mgr.accept_iteration()

        assert commit == "abc1234"

    @patch("subprocess.run")
    def test_accept_with_metrics(self, mock_run):
        """Accept with metrics should include them in the TSV."""
        responses = {
            "rev-parse --is-inside-work-tree": _make_completed_process("true\n"),
            "rev-parse --short HEAD": _make_completed_process("bbb2222\n"),
            "log -1 --format=%s": _make_completed_process("Reduced fillet\n"),
            "*": _make_completed_process(),
        }
        mock_run.side_effect = _mock_run_factory(responses)

        written_lines = []

        def capture_write(data):
            written_lines.append(data)

        m = mock_open()
        m.return_value.write = capture_write

        with patch("builtins.open", m), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True):
            mgr = GitDesignManager(repo_path="/fake/repo")
            metrics = {"mass": 0.5, "stress": 120}
            commit = mgr.accept_iteration(metrics=metrics)

        assert commit == "bbb2222"
        # Check that metrics JSON was written
        all_written = "".join(written_lines)
        assert "keep" in all_written
        assert '"mass": 0.5' in all_written or '"mass":0.5' in all_written


# ---------------------------------------------------------------------------
# TestGitDesignManager -- reject_iteration
# ---------------------------------------------------------------------------

class TestRejectIteration:
    """Tests for GitDesignManager.reject_iteration."""

    @patch("subprocess.run")
    def test_reject_without_reason(self, mock_run):
        """Reject should reset HEAD~1 and log to TSV."""
        call_idx = [0]

        def side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-parse --is-inside-work-tree" in cmd_str:
                return _make_completed_process("true\n")
            if "log -1 --format=%s" in cmd_str:
                return _make_completed_process("Bad iteration\n")
            if "reset --hard HEAD~1" in cmd_str:
                return _make_completed_process()
            if "rev-parse --short HEAD" in cmd_str:
                # First call returns discarded hash, after reset returns reverted hash
                call_idx[0] += 1
                if call_idx[0] <= 1:
                    return _make_completed_process("bad1234\n")
                return _make_completed_process("prev999\n")
            return _make_completed_process()

        mock_run.side_effect = side_effect

        with patch("builtins.open", mock_open()) as m_open, \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True):
            mgr = GitDesignManager(repo_path="/fake/repo")
            reverted = mgr.reject_iteration()

        assert reverted == "prev999"

    @patch("subprocess.run")
    def test_reject_with_reason(self, mock_run):
        """Reject with reason should include reason in log."""
        written_lines = []

        def capture_write(data):
            written_lines.append(data)

        call_idx = [0]

        def side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-parse --is-inside-work-tree" in cmd_str:
                return _make_completed_process("true\n")
            if "log -1 --format=%s" in cmd_str:
                return _make_completed_process("Thin walls\n")
            if "reset --hard" in cmd_str:
                return _make_completed_process()
            if "rev-parse --short HEAD" in cmd_str:
                call_idx[0] += 1
                if call_idx[0] <= 1:
                    return _make_completed_process("ccc3333\n")
                return _make_completed_process("bbb2222\n")
            return _make_completed_process()

        mock_run.side_effect = side_effect

        m = mock_open()
        m.return_value.write = capture_write

        with patch("builtins.open", m), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True):
            mgr = GitDesignManager(repo_path="/fake/repo")
            reverted = mgr.reject_iteration(reason="Stress too high")

        assert reverted == "bbb2222"
        all_written = "".join(written_lines)
        assert "discard" in all_written
        assert "Stress too high" in all_written


# ---------------------------------------------------------------------------
# TestGitDesignManager -- get_iteration_history
# ---------------------------------------------------------------------------

class TestGetIterationHistory:
    """Tests for GitDesignManager.get_iteration_history."""

    @patch("subprocess.run")
    def test_empty_history(self, mock_run):
        """Empty or missing TSV should return empty list."""
        mock_run.return_value = _make_completed_process("true\n")

        with patch("os.path.exists", return_value=False):
            mgr = GitDesignManager(repo_path="/fake/repo")
            history = mgr.get_iteration_history()

        assert history == []

    @patch("subprocess.run")
    def test_populated_history(self, mock_run):
        """Populated TSV should return parsed list of dicts."""
        mock_run.return_value = _make_completed_process("true\n")

        tsv_content = (
            "timestamp\tcommit\tstatus\tdescription\tmetrics\n"
            '2026-04-16T21:00:00\tabc1234\tkeep\tIncreased wall thickness\t{"mass": 0.5, "stress": 120}\n'
            '2026-04-16T21:05:00\tdef5678\tdiscard\tReduced fillet radius\t{"mass": 0.45, "stress": 200}\n'
        )

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=tsv_content)):
            mgr = GitDesignManager(repo_path="/fake/repo")
            history = mgr.get_iteration_history()

        assert len(history) == 2
        assert history[0]["commit"] == "abc1234"
        assert history[0]["status"] == "keep"
        assert history[0]["description"] == "Increased wall thickness"
        assert history[0]["metrics"] == {"mass": 0.5, "stress": 120}
        assert history[1]["commit"] == "def5678"
        assert history[1]["status"] == "discard"
        assert history[1]["metrics"] == {"mass": 0.45, "stress": 200}

    @patch("subprocess.run")
    def test_history_with_empty_metrics(self, mock_run):
        """TSV rows with empty metrics should parse metrics as None."""
        mock_run.return_value = _make_completed_process("true\n")

        tsv_content = (
            "timestamp\tcommit\tstatus\tdescription\tmetrics\n"
            "2026-04-16T21:00:00\tabc1234\tkeep\tSome change\t\n"
        )

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=tsv_content)):
            mgr = GitDesignManager(repo_path="/fake/repo")
            history = mgr.get_iteration_history()

        assert len(history) == 1
        assert history[0]["metrics"] is None

    @patch("subprocess.run")
    def test_history_with_invalid_metrics_json(self, mock_run):
        """TSV rows with invalid JSON in metrics should parse as None."""
        mock_run.return_value = _make_completed_process("true\n")

        tsv_content = (
            "timestamp\tcommit\tstatus\tdescription\tmetrics\n"
            "2026-04-16T21:00:00\tabc1234\tkeep\tSome change\tnot-valid-json\n"
        )

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=tsv_content)):
            mgr = GitDesignManager(repo_path="/fake/repo")
            history = mgr.get_iteration_history()

        assert len(history) == 1
        assert history[0]["metrics"] is None


# ---------------------------------------------------------------------------
# TestGitDesignManager -- get_current_state
# ---------------------------------------------------------------------------

class TestGetCurrentState:
    """Tests for GitDesignManager.get_current_state."""

    @patch("subprocess.run")
    def test_current_state_without_state_file(self, mock_run):
        """get_current_state without state file should return None for state_data."""
        responses = {
            "rev-parse --is-inside-work-tree": _make_completed_process("true\n"),
            "rev-parse --abbrev-ref HEAD": _make_completed_process("design/bracket\n"),
            "rev-parse --short HEAD": _make_completed_process("abc1234\n"),
        }
        mock_run.side_effect = _mock_run_factory(responses)

        with patch("os.path.exists", return_value=False):
            mgr = GitDesignManager(repo_path="/fake/repo")
            state = mgr.get_current_state()

        assert state["branch"] == "design/bracket"
        assert state["commit"] == "abc1234"
        assert state["state_data"] is None

    @patch("subprocess.run")
    def test_current_state_with_state_file(self, mock_run):
        """get_current_state with state file should return parsed JSON."""
        responses = {
            "rev-parse --is-inside-work-tree": _make_completed_process("true\n"),
            "rev-parse --abbrev-ref HEAD": _make_completed_process("design/bracket\n"),
            "rev-parse --short HEAD": _make_completed_process("def5678\n"),
        }
        mock_run.side_effect = _mock_run_factory(responses)

        state_json = json.dumps({"bodies": [{"name": "Body1"}], "mass": 0.5})

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=state_json)):
            mgr = GitDesignManager(repo_path="/fake/repo")
            state = mgr.get_current_state()

        assert state["branch"] == "design/bracket"
        assert state["commit"] == "def5678"
        assert state["state_data"]["mass"] == 0.5
        assert state["state_data"]["bodies"] == [{"name": "Body1"}]

    @patch("subprocess.run")
    def test_current_state_strips_prefix(self, mock_run):
        """Design name extraction should strip the branch prefix."""
        responses = {
            "rev-parse --is-inside-work-tree": _make_completed_process("true\n"),
            "rev-parse --abbrev-ref HEAD": _make_completed_process("design/my_part\n"),
            "rev-parse --short HEAD": _make_completed_process("aaa1111\n"),
        }
        mock_run.side_effect = _mock_run_factory(responses)

        with patch("os.path.exists", return_value=False):
            mgr = GitDesignManager(repo_path="/fake/repo")
            # The internal design name should be "my_part"
            name = mgr._design_name_from_branch()
            assert name == "my_part"

    @patch("subprocess.run")
    def test_current_state_non_design_branch(self, mock_run):
        """If not on a design branch, design name should be the full branch name."""
        responses = {
            "rev-parse --is-inside-work-tree": _make_completed_process("true\n"),
            "rev-parse --abbrev-ref HEAD": _make_completed_process("main\n"),
            "rev-parse --short HEAD": _make_completed_process("bbb2222\n"),
        }
        mock_run.side_effect = _mock_run_factory(responses)

        with patch("os.path.exists", return_value=False):
            mgr = GitDesignManager(repo_path="/fake/repo")
            name = mgr._design_name_from_branch()
            assert name == "main"


# ---------------------------------------------------------------------------
# TestGitDesignManager -- TSV log
# ---------------------------------------------------------------------------

class TestTSVLog:
    """Tests for the TSV log creation and header."""

    @patch("subprocess.run")
    def test_ensure_iterations_file_creates_header(self, mock_run):
        """First call should create file with TSV header."""
        mock_run.return_value = _make_completed_process("true\n")

        m = mock_open()
        with patch("builtins.open", m), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False):
            mgr = GitDesignManager(repo_path="/fake/repo")
            mgr._ensure_iterations_file()

        m.assert_called()
        handle = m()
        handle.write.assert_called_with(_TSV_HEADER)

    @patch("subprocess.run")
    def test_ensure_iterations_file_skips_existing(self, mock_run):
        """If file exists, _ensure_iterations_file should not recreate it."""
        mock_run.return_value = _make_completed_process("true\n")

        m = mock_open()
        with patch("builtins.open", m), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True):
            mgr = GitDesignManager(repo_path="/fake/repo")
            mgr._ensure_iterations_file()

        # open should not be called since the file exists
        m.assert_not_called()


# ---------------------------------------------------------------------------
# TestDesignStateTracker -- git_manager integration
# ---------------------------------------------------------------------------

class TestDesignStateTrackerGitIntegration:
    """Tests for DesignStateTracker with optional git_manager."""

    def test_init_without_git_manager(self):
        """Default construction should have git_manager=None."""
        from ai.design_state_tracker import DesignStateTracker
        tracker = DesignStateTracker()
        assert tracker._git_manager is None

    def test_init_with_git_manager(self):
        """Construction with git_manager should store it."""
        from ai.design_state_tracker import DesignStateTracker
        mock_git = MagicMock()
        tracker = DesignStateTracker(git_manager=mock_git)
        assert tracker._git_manager is mock_git

    def test_update_calls_git_checkpoint(self):
        """When git_manager is set, update() should call checkpoint."""
        from ai.design_state_tracker import DesignStateTracker

        mock_git = MagicMock()
        tracker = DesignStateTracker(git_manager=mock_git)

        # Create a mock MCP server that returns some data
        mcp = MagicMock()

        def execute_tool(name, params):
            if name == "get_body_list":
                return {"success": True, "bodies": [], "component_count": 0}
            if name == "get_timeline":
                return {"success": True, "timeline": []}
            if name == "get_sketch_list":
                return {"success": True, "sketches": []}
            return {"success": False}

        mcp.execute_tool = MagicMock(side_effect=execute_tool)
        tracker.update(mcp)

        mock_git.checkpoint.assert_called_once()
        call_args = mock_git.checkpoint.call_args
        assert "Design state update:" in call_args[0][0]
        assert call_args[1]["state_data"] is not None

    def test_update_without_git_manager_no_error(self):
        """When git_manager is None, update should work normally."""
        from ai.design_state_tracker import DesignStateTracker

        tracker = DesignStateTracker()  # no git_manager

        mcp = MagicMock()

        def execute_tool(name, params):
            if name == "get_body_list":
                return {"success": True, "bodies": [], "component_count": 0}
            if name == "get_timeline":
                return {"success": True, "timeline": []}
            if name == "get_sketch_list":
                return {"success": True, "sketches": []}
            return {"success": False}

        mcp.execute_tool = MagicMock(side_effect=execute_tool)
        tracker.update(mcp)  # should not raise

    def test_git_checkpoint_failure_is_graceful(self):
        """If git checkpoint fails, update should still succeed."""
        from ai.design_state_tracker import DesignStateTracker

        mock_git = MagicMock()
        mock_git.checkpoint.side_effect = RuntimeError("git error")
        tracker = DesignStateTracker(git_manager=mock_git)

        mcp = MagicMock()

        def execute_tool(name, params):
            if name == "get_body_list":
                return {"success": True, "bodies": [], "component_count": 0}
            if name == "get_timeline":
                return {"success": True, "timeline": []}
            if name == "get_sketch_list":
                return {"success": True, "sketches": []}
            return {"success": False}

        mcp.execute_tool = MagicMock(side_effect=execute_tool)
        tracker.update(mcp)  # should not raise


# ---------------------------------------------------------------------------
# TestCheckpointManager -- git_manager integration
# ---------------------------------------------------------------------------

class TestCheckpointManagerGitIntegration:
    """Tests for CheckpointManager with optional git_manager."""

    def _mock_mcp(self):
        """Create a mock MCP server."""
        server = MagicMock()

        def execute_tool(name, params):
            if name == "get_timeline":
                return {"success": True, "timeline": [{"index": 0}]}
            elif name == "get_body_list":
                return {"success": True, "count": 1}
            return {"success": False}

        server.execute_tool = MagicMock(side_effect=execute_tool)
        return server

    def test_init_without_git_manager(self):
        """Default construction should have git_manager=None."""
        from ai.checkpoint_manager import CheckpointManager
        mgr = CheckpointManager()
        assert mgr._git_manager is None

    def test_init_with_git_manager(self):
        """Construction with git_manager should store it."""
        from ai.checkpoint_manager import CheckpointManager
        mock_git = MagicMock()
        mgr = CheckpointManager(git_manager=mock_git)
        assert mgr._git_manager is mock_git

    def test_save_calls_git_checkpoint(self):
        """When git_manager is set, save() should call git checkpoint."""
        from ai.checkpoint_manager import CheckpointManager
        mock_git = MagicMock()
        mgr = CheckpointManager(git_manager=mock_git)
        mcp = self._mock_mcp()

        mgr.save("v1", mcp, message_count=5, description="first version")
        mock_git.checkpoint.assert_called_once()
        call_args = mock_git.checkpoint.call_args
        assert "Checkpoint: v1" in call_args[0][0]
        assert "first version" in call_args[0][0]

    def test_save_without_git_manager_no_error(self):
        """When git_manager is None, save should work normally."""
        from ai.checkpoint_manager import CheckpointManager
        mgr = CheckpointManager()
        mcp = self._mock_mcp()
        cp = mgr.save("v1", mcp, message_count=5)
        assert cp.name == "v1"

    def test_git_checkpoint_failure_is_graceful(self):
        """If git checkpoint fails, save should still succeed."""
        from ai.checkpoint_manager import CheckpointManager
        mock_git = MagicMock()
        mock_git.checkpoint.side_effect = RuntimeError("git error")
        mgr = CheckpointManager(git_manager=mock_git)
        mcp = self._mock_mcp()

        cp = mgr.save("v1", mcp, message_count=5)
        assert cp.name == "v1"
        assert mgr.count == 1

    def test_save_without_description(self):
        """Save without description should not include separator in commit msg."""
        from ai.checkpoint_manager import CheckpointManager
        mock_git = MagicMock()
        mgr = CheckpointManager(git_manager=mock_git)
        mcp = self._mock_mcp()

        mgr.save("v1", mcp, message_count=5)
        call_args = mock_git.checkpoint.call_args
        commit_msg = call_args[0][0]
        assert commit_msg == "Checkpoint: v1"
        assert " -- " not in commit_msg


# ---------------------------------------------------------------------------
# TestConfigSettings -- new git design settings
# ---------------------------------------------------------------------------

class TestConfigSettingsGitDesign:
    """Tests for the new git design tracking settings."""

    def test_defaults_include_git_settings(self):
        """DEFAULTS dict should contain the new git design settings."""
        from config.settings import DEFAULTS
        assert "git_design_tracking_enabled" in DEFAULTS
        assert DEFAULTS["git_design_tracking_enabled"] is False
        assert "git_design_branch_prefix" in DEFAULTS
        assert DEFAULTS["git_design_branch_prefix"] == "design"
        assert "git_design_state_dir" in DEFAULTS
        assert DEFAULTS["git_design_state_dir"] == "data/design_states"

    def test_settings_instance_has_git_values(self):
        """Settings instance should expose the new git settings via get()."""
        from config.settings import Settings
        s = Settings()
        # Force defaults (no config file)
        s._loaded = True
        assert s.get("git_design_tracking_enabled") is False
        assert s.get("git_design_branch_prefix") == "design"
        assert s.get("git_design_state_dir") == "data/design_states"

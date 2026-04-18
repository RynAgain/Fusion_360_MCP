"""
ai/git_design_manager.py
Git-based design state management -- uses git branches as a state machine
for tracking Fusion 360 design iterations.

Inspired by Karpathy's autoresearch pattern:
- Branch = current best design configuration
- Commit = design iteration attempt
- git reset = discard failed iteration
- Branch advance = accepted improvement

All git operations use subprocess (no gitpython dependency).
"""

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)

# TSV header for the iteration log
_TSV_HEADER = "timestamp\tcommit\tstatus\tdescription\tmetrics\n"

# TASK-059: Pattern for safe branch/design names to prevent git argument injection
_SAFE_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')


def _validate_name(name: str, label: str = "name") -> str:
    """Validate a name is safe for use in git commands."""
    if not name or not _SAFE_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid {label}: must match [a-zA-Z0-9._-], got {name!r}")
    return name


class GitDesignManager:
    """Manages design iterations using git as a state machine.

    Pattern from autoresearch:
    - Branch = current best design configuration
    - Commit = design iteration attempt
    - git reset = discard failed iteration
    - Branch advance = accepted improvement
    """

    def __init__(self, repo_path: str = ".", branch_prefix: str = "design") -> None:
        """Initialize with path to git repo and branch naming prefix.

        Args:
            repo_path: Path to the git repository root.
            branch_prefix: Prefix for design iteration branches.

        Raises:
            ValueError: If the path is not a git repository.
        """
        self._repo_path = os.path.abspath(repo_path)
        self._branch_prefix = branch_prefix
        self._iterations_file = os.path.join(self._repo_path, "data", "design_iterations.tsv")
        self._state_dir = os.path.join(self._repo_path, "data", "design_states")

        # Validate that repo_path is a git repo
        result = self._git("rev-parse", "--is-inside-work-tree")
        if result.returncode != 0:
            raise ValueError(
                f"Path is not a git repository: {self._repo_path}"
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        """Run a git command and return the CompletedProcess."""
        cmd = ["git", "-C", self._repo_path] + list(args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _current_branch(self) -> str:
        """Return the name of the currently checked-out branch."""
        result = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()

    def _current_commit(self) -> str:
        """Return the short hash of the current HEAD commit."""
        result = self._git("rev-parse", "--short", "HEAD")
        return result.stdout.strip()

    def _branch_exists(self, branch_name: str) -> bool:
        """Check whether a local branch exists."""
        result = self._git("show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}")
        return result.returncode == 0

    def _design_name_from_branch(self) -> str:
        """Extract the design name from the current branch (strip prefix)."""
        branch = self._current_branch()
        prefix = f"{self._branch_prefix}/"
        if branch.startswith(prefix):
            return branch[len(prefix):]
        return branch

    def _ensure_iterations_file(self) -> None:
        """Ensure the TSV log file exists with a header row."""
        os.makedirs(os.path.dirname(self._iterations_file), exist_ok=True)
        if not os.path.exists(self._iterations_file):
            with open(self._iterations_file, "w", encoding="utf-8") as f:
                f.write(_TSV_HEADER)

    def _append_iteration_log(
        self, commit: str, status: str, description: str, metrics: dict | None = None,
    ) -> None:
        """Append a row to the design_iterations.tsv log.

        Uses ``flush`` + ``os.fsync`` to ensure the write is durable.
        """
        self._ensure_iterations_file()
        timestamp = datetime.now(timezone.utc).isoformat()
        metrics_str = json.dumps(metrics) if metrics else ""
        row = f"{timestamp}\t{commit}\t{status}\t{description}\t{metrics_str}\n"
        with open(self._iterations_file, "a", encoding="utf-8") as f:
            f.write(row)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, TypeError, ValueError):
                pass  # fsync may fail on non-real file descriptors (e.g. tests)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_iteration(self, design_name: str) -> str:
        """Create a new branch for a design iteration.

        Creates ``{branch_prefix}/{design_name}`` from the current state.
        If the branch already exists, switches to it.

        Args:
            design_name: Human-readable name for this design iteration.

        Returns:
            The full branch name.
        """
        # TASK-059: Validate design_name to prevent git argument injection
        _validate_name(design_name, "design_name")
        branch_name = f"{self._branch_prefix}/{design_name}"

        if self._branch_exists(branch_name):
            self._git("checkout", branch_name)
            logger.info("Switched to existing design branch: %s", branch_name)
        else:
            self._git("checkout", "-b", branch_name)
            logger.info("Created new design branch: %s", branch_name)

        return branch_name

    def checkpoint(self, description: str, state_data: dict | None = None) -> str:
        """Commit the current state with a descriptive message.

        Optionally writes *state_data* to a JSON file in the design states
        directory before committing.

        Args:
            description: Commit message describing this checkpoint.
            state_data: Optional dict to persist as a JSON state file.

        Returns:
            The commit hash of the new checkpoint.
        """
        # Write state data to JSON if provided
        if state_data is not None:
            os.makedirs(self._state_dir, exist_ok=True)
            design_name = self._design_name_from_branch()
            state_file = os.path.join(self._state_dir, f"{design_name}_state.json")
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
            self._git("add", state_file)

        # Stage all changes and commit
        self._git("add", "-A")
        self._git("commit", "-m", description, "--allow-empty")

        commit_hash = self._current_commit()
        logger.info("Checkpoint created: %s -- %s", commit_hash, description)
        return commit_hash

    def accept_iteration(self, metrics: dict | None = None) -> str:
        """Mark the current commit as a keeper -- advance the branch.

        Optionally logs metrics to the design_iterations.tsv append-only log.

        Args:
            metrics: Optional dict of performance/quality metrics.

        Returns:
            The commit hash that was accepted.
        """
        commit_hash = self._current_commit()

        # Read the last commit message for the log description
        result = self._git("log", "-1", "--format=%s")
        description = result.stdout.strip()

        self._append_iteration_log(commit_hash, "keep", description, metrics)
        logger.info("Iteration accepted: %s", commit_hash)
        return commit_hash

    def reject_iteration(self, reason: str = "") -> str:
        """Discard the current iteration by resetting to the previous commit.

        Logs the rejection to design_iterations.tsv, then performs
        ``git reset --hard HEAD~1``.

        Args:
            reason: Optional human-readable reason for rejection.

        Returns:
            The commit hash we reverted to.
        """
        # Get current commit info before discarding
        discarded_hash = self._current_commit()
        result = self._git("log", "-1", "--format=%s")
        description = result.stdout.strip()

        log_description = description
        if reason:
            log_description = f"{description} -- rejected: {reason}"

        self._append_iteration_log(discarded_hash, "discard", log_description)

        # Check commit count before resetting to avoid errors on single-commit repos
        result = self._git("rev-list", "--count", "HEAD")
        count_str = result.stdout.strip() if result.returncode == 0 else ""
        try:
            count = int(count_str) if count_str else 2  # default: assume >1
        except (ValueError, TypeError):
            count = 2  # default: assume >1 so we use the standard reset
        if count <= 1:
            # Can't reset with only one commit -- delete the ref instead
            self._git("update-ref", "-d", "HEAD")
        else:
            self._git("reset", "--hard", "HEAD~1")

        reverted_hash = self._current_commit()
        logger.info(
            "Iteration rejected: %s -> reverted to %s (reason: %s)",
            discarded_hash, reverted_hash, reason or "none",
        )
        return reverted_hash

    def get_iteration_history(self) -> list[dict[str, Any]]:
        """Read and parse the design_iterations.tsv log.

        Returns:
            List of dicts with keys: timestamp, commit, status,
            description, metrics.
        """
        if not os.path.exists(self._iterations_file):
            return []

        rows: list[dict[str, Any]] = []
        with open(self._iterations_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Skip header
        for line in lines[1:]:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t", 4)
            if len(parts) < 5:
                continue
            timestamp, commit, status, description, metrics_str = parts
            metrics: dict | None = None
            if metrics_str:
                try:
                    metrics = json.loads(metrics_str)
                except (json.JSONDecodeError, ValueError):
                    metrics = None
            rows.append({
                "timestamp": timestamp,
                "commit": commit,
                "status": status,
                "description": description,
                "metrics": metrics,
            })

        return rows

    def get_current_state(self) -> dict[str, Any]:
        """Return current branch name, commit hash, and any state JSON data.

        Returns:
            Dict with keys: branch, commit, state_data (or None).
        """
        branch = self._current_branch()
        commit = self._current_commit()

        # Try to load the state JSON for the current design
        design_name = self._design_name_from_branch()
        state_file = os.path.join(self._state_dir, f"{design_name}_state.json")
        state_data: dict | None = None
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read state file %s: %s", state_file, exc)

        return {
            "branch": branch,
            "commit": commit,
            "state_data": state_data,
        }

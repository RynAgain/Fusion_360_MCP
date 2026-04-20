"""
ai/ignore_controller.py
File access control for the AI agent.

Uses .gitignore-style patterns from an .artifexignore file to prevent the agent
from reading sensitive files (API keys, credentials, private keys, etc.).

TASK-186: Replaced fnmatch with pathspec library for correct gitignore
semantics (recursive globs, ** patterns, leading /, trailing /, negation).
"""
import logging
import os
from pathlib import Path

import pathspec

logger = logging.getLogger(__name__)


class IgnoreController:
    """Controls which files the AI agent can access.

    Loads patterns from .artifexignore (if it exists) in the project root.
    Uses .gitignore-style pattern matching via the pathspec library
    (gitwildmatch flavour).

    Default built-in patterns always block:
    - .env* files
    - *.key, *.pem, *.p12, *.pfx (private keys)
    - **/secrets/*, **/credentials/*
    - .git/** (git internals)
    """

    # Built-in patterns that are always enforced (cannot be overridden)
    BUILTIN_PATTERNS = [
        ".env",
        ".env.*",
        "*.key",
        "*.pem",
        "*.p12",
        "*.pfx",
        "**/.git/**",
        "**/secrets/**",
        "**/credentials/**",
        "**/__pycache__/**",
        "*.pyc",
    ]

    def __init__(self, project_root: str | None = None):
        self._project_root = project_root or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        self._custom_patterns: list[str] = []
        self._load_ignore_file()
        self._rebuild_spec()

    def _rebuild_spec(self) -> None:
        """Rebuild the compiled pathspec from all active patterns."""
        try:
            self._spec = pathspec.PathSpec.from_lines(
                "gitignore", self.all_patterns,
            )
        except ValueError:
            # Older pathspec versions use 'gitwildmatch'
            self._spec = pathspec.PathSpec.from_lines(
                "gitwildmatch", self.all_patterns,
            )

    def _load_ignore_file(self) -> None:
        """Load patterns from .artifexignore file if it exists."""
        ignore_path = os.path.join(self._project_root, ".artifexignore")
        if os.path.exists(ignore_path):
            try:
                with open(ignore_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        # Skip empty lines and comments
                        if line and not line.startswith("#"):
                            self._custom_patterns.append(line)
                logger.info(
                    "Loaded %d patterns from .artifexignore",
                    len(self._custom_patterns),
                )
            except OSError as exc:
                logger.warning("Failed to load .artifexignore: %s", exc)

    def reload(self) -> None:
        """Reload patterns from .artifexignore file."""
        self._custom_patterns.clear()
        self._load_ignore_file()
        self._rebuild_spec()

    @property
    def all_patterns(self) -> list[str]:
        """Return all active patterns (built-in + custom)."""
        return list(self.BUILTIN_PATTERNS) + self._custom_patterns

    def is_blocked(self, file_path: str) -> bool:
        """Check if a file path is blocked by ignore patterns.

        Args:
            file_path: Path to check (absolute or relative to project root).

        Returns:
            True if the file should be blocked from agent access.
        """
        # Normalize to relative path with forward slashes
        try:
            if os.path.isabs(file_path):
                rel_path = os.path.relpath(file_path, self._project_root)
            else:
                # Already relative -- use as-is to avoid CWD dependency
                rel_path = file_path
        except (ValueError, TypeError):
            rel_path = file_path

        rel_path = rel_path.replace("\\", "/")

        if self._spec.match_file(rel_path):
            logger.debug("File blocked: %s", rel_path)
            return True

        return False

    def filter_paths(self, paths: list[str]) -> list[str]:
        """Filter a list of paths, returning only allowed ones."""
        return [p for p in paths if not self.is_blocked(p)]


# Module-level singleton
_ignore_controller: IgnoreController | None = None


def get_ignore_controller() -> IgnoreController:
    """Get or create the singleton IgnoreController."""
    global _ignore_controller
    if _ignore_controller is None:
        _ignore_controller = IgnoreController()
    return _ignore_controller


def reset_ignore_controller() -> None:
    """Reset the singleton IgnoreController. Intended for testing."""
    global _ignore_controller
    _ignore_controller = None

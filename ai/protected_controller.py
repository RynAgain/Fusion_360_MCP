"""
ai/protected_controller.py
Write-protection for sensitive configuration files.

Prevents the AI agent from modifying critical configuration files,
even when auto-approval is enabled. Protected files always require
explicit user confirmation.

TASK-211: Replaced fnmatch with pathspec library for correct gitignore
semantics (recursive globs, ** patterns), aligning with ignore_controller.
"""
import logging
import os

import pathspec

logger = logging.getLogger(__name__)


class ProtectedController:
    """Controls write access to sensitive configuration files.

    Files matching protected patterns require explicit user confirmation
    before the agent can modify them.

    Uses .gitignore-style pattern matching via the pathspec library
    (gitwildmatch flavour), consistent with IgnoreController (TASK-211).
    """

    # Protected patterns -- these files cannot be modified without user confirmation
    PROTECTED_PATTERNS = [
        "config/**",
        ".env",
        ".env.*",
        ".artifexignore",
        ".artifexmodes",
        "*.manifest",
        "*.key",
        "*.pem",
        "*.p12",
        "*.pfx",
        "requirements.txt",
        "setup.py",
        "setup.cfg",
        "pyproject.toml",
        "main.py",
        ".gitignore",
        ".gitattributes",
    ]

    def __init__(self, project_root: str | None = None):
        self._project_root = project_root or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        self._rebuild_spec()

    def _rebuild_spec(self) -> None:
        """Rebuild the compiled pathspec from protected patterns."""
        try:
            self._spec = pathspec.PathSpec.from_lines(
                "gitignore", self.PROTECTED_PATTERNS,
            )
        except ValueError:
            # Older pathspec versions use 'gitwildmatch'
            self._spec = pathspec.PathSpec.from_lines(
                "gitwildmatch", self.PROTECTED_PATTERNS,
            )

    def is_protected(self, file_path: str) -> bool:
        """Check if a file is write-protected.

        Args:
            file_path: Path to check (absolute or relative to project root).

        Returns:
            True if the file requires explicit confirmation to modify.
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
            return True

        return False


# Module-level singleton
_protected_controller: ProtectedController | None = None


def get_protected_controller() -> ProtectedController:
    """Get or create the singleton ProtectedController."""
    global _protected_controller
    if _protected_controller is None:
        _protected_controller = ProtectedController()
    return _protected_controller


def reset_protected_controller() -> None:
    """Reset the singleton ProtectedController. Intended for testing."""
    global _protected_controller
    _protected_controller = None

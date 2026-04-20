"""
tests/test_file_tools.py
Tests for mcp/file_tools.py -- apply_diff and write_file operations.
"""
import os
import pytest
from unittest.mock import patch, MagicMock

from mcp.file_tools import apply_diff, write_file


# ---------------------------------------------------------------------------
# apply_diff tests
# ---------------------------------------------------------------------------


class TestApplyDiff:
    """Tests for the apply_diff function."""

    def test_succeeds_on_matching_text(self, tmp_path):
        """apply_diff replaces matching text and returns success."""
        target = tmp_path / "hello.txt"
        target.write_text("Hello, world!", encoding="utf-8")

        result = apply_diff(
            "hello.txt", "world", "Python", project_root=str(tmp_path),
        )

        assert result["success"] is True
        assert result["file"] == "hello.txt"
        assert result["occurrences_found"] == 1
        assert result["replaced"] == 1
        assert result["chars_removed"] == len("world")
        assert result["chars_added"] == len("Python")
        assert target.read_text(encoding="utf-8") == "Hello, Python!"

    def test_fails_when_search_text_not_found(self, tmp_path):
        """apply_diff returns error when search text is not in the file."""
        target = tmp_path / "hello.txt"
        target.write_text("Hello, world!", encoding="utf-8")

        result = apply_diff(
            "hello.txt", "missing", "replacement", project_root=str(tmp_path),
        )

        assert result["success"] is False
        assert "not found" in result["error"]
        assert result["file_length"] == len("Hello, world!")

    def test_fails_on_path_traversal(self, tmp_path):
        """apply_diff rejects paths that escape the project root."""
        target = tmp_path / "hello.txt"
        target.write_text("content", encoding="utf-8")

        result = apply_diff(
            "../../../etc/passwd", "root", "hacked",
            project_root=str(tmp_path),
        )

        assert result["success"] is False
        assert "traversal" in result["error"].lower()

    @patch("ai.protected_controller.get_protected_controller")
    def test_blocks_protected_files(self, mock_get_ctrl, tmp_path):
        """apply_diff refuses to modify write-protected files."""
        ctrl = MagicMock()
        ctrl.is_protected.return_value = True
        mock_get_ctrl.return_value = ctrl

        target = tmp_path / "config.json"
        target.write_text("{}", encoding="utf-8")

        result = apply_diff(
            "config.json", "{}", "{}", project_root=str(tmp_path),
        )

        assert result["success"] is False
        assert result.get("protected") is True

    @patch("ai.ignore_controller.get_ignore_controller")
    @patch("ai.protected_controller.get_protected_controller")
    def test_blocks_ignored_files(self, mock_prot, mock_ign, tmp_path):
        """apply_diff refuses to modify files blocked by ignore patterns."""
        prot_ctrl = MagicMock()
        prot_ctrl.is_protected.return_value = False
        mock_prot.return_value = prot_ctrl

        ign_ctrl = MagicMock()
        ign_ctrl.is_blocked.return_value = True
        mock_ign.return_value = ign_ctrl

        target = tmp_path / "secret.key"
        target.write_text("secret", encoding="utf-8")

        result = apply_diff(
            "secret.key", "secret", "replaced", project_root=str(tmp_path),
        )

        assert result["success"] is False
        assert "blocked" in result["error"].lower()

    def test_handles_missing_files(self, tmp_path):
        """apply_diff returns error for non-existent files."""
        result = apply_diff(
            "nonexistent.txt", "a", "b", project_root=str(tmp_path),
        )

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_replaces_first_occurrence_only(self, tmp_path):
        """apply_diff replaces only the first occurrence when multiple exist."""
        target = tmp_path / "multi.txt"
        target.write_text("aaa", encoding="utf-8")

        result = apply_diff("multi.txt", "a", "b", project_root=str(tmp_path))

        assert result["success"] is True
        assert result["occurrences_found"] == 3
        assert result["replaced"] == 1
        assert target.read_text(encoding="utf-8") == "baa"


# ---------------------------------------------------------------------------
# write_file tests
# ---------------------------------------------------------------------------


class TestWriteFile:
    """Tests for the write_file function."""

    def test_creates_new_file(self, tmp_path):
        """write_file creates a new file and returns created=True."""
        result = write_file(
            "new_file.txt", "hello", project_root=str(tmp_path),
        )

        assert result["success"] is True
        assert result["created"] is True
        assert result["bytes_written"] == len("hello".encode("utf-8"))
        assert (tmp_path / "new_file.txt").read_text(encoding="utf-8") == "hello"

    def test_overwrites_existing_file(self, tmp_path):
        """write_file overwrites an existing file and returns created=False."""
        target = tmp_path / "existing.txt"
        target.write_text("old content", encoding="utf-8")

        result = write_file(
            "existing.txt", "new content", project_root=str(tmp_path),
        )

        assert result["success"] is True
        assert result["created"] is False
        assert target.read_text(encoding="utf-8") == "new content"

    def test_creates_directories(self, tmp_path):
        """write_file creates parent directories when create_dirs=True."""
        result = write_file(
            "sub/dir/deep.txt", "nested", project_root=str(tmp_path),
        )

        assert result["success"] is True
        assert result["created"] is True
        assert (tmp_path / "sub" / "dir" / "deep.txt").read_text(encoding="utf-8") == "nested"

    def test_fails_on_path_traversal(self, tmp_path):
        """write_file rejects paths that escape the project root."""
        result = write_file(
            "../../../etc/evil", "payload", project_root=str(tmp_path),
        )

        assert result["success"] is False
        assert "traversal" in result["error"].lower()

    @patch("ai.protected_controller.get_protected_controller")
    def test_blocks_protected_files(self, mock_get_ctrl, tmp_path):
        """write_file refuses to write to protected files."""
        ctrl = MagicMock()
        ctrl.is_protected.return_value = True
        mock_get_ctrl.return_value = ctrl

        result = write_file(
            "config.json", "{}", project_root=str(tmp_path),
        )

        assert result["success"] is False
        assert result.get("protected") is True

    def test_bytes_written_counts_utf8(self, tmp_path):
        """write_file counts bytes correctly for multi-byte characters."""
        content = "cafe\u0301"  # 'e' + combining accent = multi-byte UTF-8
        result = write_file(
            "unicode.txt", content, project_root=str(tmp_path),
        )

        assert result["success"] is True
        assert result["bytes_written"] == len(content.encode("utf-8"))

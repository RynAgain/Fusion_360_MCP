"""
tests/test_file_context_tracker.py
Tests for the file context tracking module.
"""
import hashlib
import os
import time

import pytest

from ai.file_context_tracker import FileContextTracker, TrackedFile


class TestRecordRead:
    """Tests for recording file reads."""

    def test_record_read_with_string_content(self):
        """record_read with explicit string content creates a tracked entry."""
        tracker = FileContextTracker()
        tracker.record_read("/tmp/test.py", content="hello world")
        assert tracker.is_tracked("/tmp/test.py")

    def test_record_read_with_bytes_content(self):
        """record_read with explicit bytes content creates a tracked entry."""
        tracker = FileContextTracker()
        tracker.record_read("/tmp/test.py", content=b"hello world")
        assert tracker.is_tracked("/tmp/test.py")

    def test_record_read_from_disk(self, tmp_path):
        """record_read with no content reads the file from disk."""
        f = tmp_path / "sample.txt"
        f.write_text("disk content", encoding="utf-8")
        tracker = FileContextTracker()
        tracker.record_read(str(f))
        assert tracker.is_tracked(str(f))

    def test_record_read_missing_file_no_error(self):
        """record_read with a non-existent file and no content silently skips."""
        tracker = FileContextTracker()
        tracker.record_read("/nonexistent/path/xyz.txt")
        assert not tracker.is_tracked("/nonexistent/path/xyz.txt")

    def test_read_count_increments(self):
        """read_count increments on multiple reads of the same file."""
        tracker = FileContextTracker()
        tracker.record_read("/tmp/test.py", content="v1")
        tracker.record_read("/tmp/test.py", content="v2")
        tracker.record_read("/tmp/test.py", content="v3")
        data = tracker.to_dict()
        abs_path = os.path.abspath("/tmp/test.py")
        assert data[abs_path]["read_count"] == 3


class TestCheckModified:
    """Tests for checking external file modifications."""

    def test_unmodified_file_returns_false(self, tmp_path):
        """check_modified returns False when file has not changed."""
        f = tmp_path / "stable.txt"
        f.write_text("original", encoding="utf-8")
        tracker = FileContextTracker()
        tracker.record_read(str(f))
        assert tracker.check_modified(str(f)) is False

    def test_modified_file_returns_true(self, tmp_path):
        """check_modified returns True after file is changed externally."""
        f = tmp_path / "changing.txt"
        f.write_text("original", encoding="utf-8")
        tracker = FileContextTracker()
        tracker.record_read(str(f))
        # Modify externally
        f.write_text("modified", encoding="utf-8")
        assert tracker.check_modified(str(f)) is True

    def test_untracked_file_returns_false(self):
        """check_modified returns False for files that are not tracked."""
        tracker = FileContextTracker()
        assert tracker.check_modified("/some/random/file.txt") is False

    def test_deleted_file_returns_false(self, tmp_path):
        """check_modified returns False if the file was deleted (OSError)."""
        f = tmp_path / "ephemeral.txt"
        f.write_text("temp", encoding="utf-8")
        tracker = FileContextTracker()
        tracker.record_read(str(f))
        f.unlink()
        assert tracker.check_modified(str(f)) is False


class TestGetStaleFiles:
    """Tests for get_stale_files."""

    def test_returns_modified_files(self, tmp_path):
        """get_stale_files returns files that have been modified externally."""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("a-content", encoding="utf-8")
        f2.write_text("b-content", encoding="utf-8")

        tracker = FileContextTracker()
        tracker.record_read(str(f1))
        tracker.record_read(str(f2))

        # Modify only f1
        f1.write_text("a-modified", encoding="utf-8")

        stale = tracker.get_stale_files()
        assert len(stale) == 1
        assert os.path.abspath(str(f1)) in stale

    def test_returns_empty_when_none_modified(self, tmp_path):
        """get_stale_files returns empty list when nothing changed."""
        f = tmp_path / "stable.txt"
        f.write_text("content", encoding="utf-8")
        tracker = FileContextTracker()
        tracker.record_read(str(f))
        assert tracker.get_stale_files() == []


class TestTracking:
    """Tests for is_tracked and get_tracked_files."""

    def test_is_tracked_returns_true_for_tracked(self):
        """is_tracked returns True for a tracked file."""
        tracker = FileContextTracker()
        tracker.record_read("/tmp/tracked.py", content="x")
        assert tracker.is_tracked("/tmp/tracked.py") is True

    def test_is_tracked_returns_false_for_untracked(self):
        """is_tracked returns False for an untracked file."""
        tracker = FileContextTracker()
        assert tracker.is_tracked("/tmp/unknown.py") is False

    def test_get_tracked_files_returns_all_paths(self):
        """get_tracked_files returns all tracked absolute paths."""
        tracker = FileContextTracker()
        tracker.record_read("/tmp/a.py", content="a")
        tracker.record_read("/tmp/b.py", content="b")
        paths = tracker.get_tracked_files()
        assert len(paths) == 2
        assert os.path.abspath("/tmp/a.py") in paths
        assert os.path.abspath("/tmp/b.py") in paths


class TestUntrackAndReset:
    """Tests for untrack and reset."""

    def test_untrack_removes_file(self):
        """untrack removes a specific file from tracking."""
        tracker = FileContextTracker()
        tracker.record_read("/tmp/a.py", content="a")
        tracker.record_read("/tmp/b.py", content="b")
        tracker.untrack("/tmp/a.py")
        assert not tracker.is_tracked("/tmp/a.py")
        assert tracker.is_tracked("/tmp/b.py")

    def test_untrack_nonexistent_is_noop(self):
        """untrack on a non-tracked file does not raise."""
        tracker = FileContextTracker()
        tracker.untrack("/tmp/nope.py")  # should not raise

    def test_reset_clears_all(self):
        """reset clears all tracked files."""
        tracker = FileContextTracker()
        tracker.record_read("/tmp/a.py", content="a")
        tracker.record_read("/tmp/b.py", content="b")
        tracker.reset()
        assert tracker.get_tracked_files() == []


class TestToDict:
    """Tests for serialization."""

    def test_to_dict_returns_serializable_data(self):
        """to_dict returns a dict with expected keys for each tracked file."""
        tracker = FileContextTracker()
        tracker.record_read("/tmp/test.py", content="hello")
        data = tracker.to_dict()
        abs_path = os.path.abspath("/tmp/test.py")
        assert abs_path in data
        entry = data[abs_path]
        assert "content_hash" in entry
        assert entry["content_hash"].endswith("...")
        assert "last_read" in entry
        assert isinstance(entry["last_read"], float)
        assert "read_count" in entry
        assert entry["read_count"] == 1

    def test_to_dict_empty_tracker(self):
        """to_dict returns empty dict when no files are tracked."""
        tracker = FileContextTracker()
        assert tracker.to_dict() == {}


# ---------------------------------------------------------------------------
# TASK-198: Chunked file hash computation
# ---------------------------------------------------------------------------

class TestComputeFileHash:
    """Tests for _compute_file_hash -- chunked SHA-256 computation."""

    def test_hash_matches_expected_sha256(self, tmp_path):
        """Hash of a small file should match hashlib.sha256 computed in one shot."""
        f = tmp_path / "small.txt"
        content = b"hello world"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert FileContextTracker._compute_file_hash(str(f)) == expected

    def test_hash_large_file_exceeding_chunk_size(self, tmp_path):
        """File larger than 8KB should still produce correct SHA-256 hash."""
        f = tmp_path / "large.bin"
        # 32KB of pseudo-random but deterministic data (4x the 8KB chunk)
        content = bytes(range(256)) * 128  # 32,768 bytes
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        result = FileContextTracker._compute_file_hash(str(f))
        assert result == expected
        assert len(content) > 8192, "Test data must exceed the 8KB chunk size"

    def test_nonexistent_file_returns_empty_string(self):
        """Non-existent file should return empty string, not raise."""
        result = FileContextTracker._compute_file_hash("/nonexistent/path/xyz.bin")
        assert result == ""

    def test_hash_empty_file(self, tmp_path):
        """Empty file should produce the SHA-256 of b''."""
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert FileContextTracker._compute_file_hash(str(f)) == expected

    def test_record_read_uses_chunked_hash_for_disk_reads(self, tmp_path):
        """record_read with no content arg should use chunked hashing and
        produce the same hash as an explicit content-based read."""
        f = tmp_path / "chunked_test.bin"
        content = bytes(range(256)) * 64  # 16KB
        f.write_bytes(content)

        tracker_disk = FileContextTracker()
        tracker_disk.record_read(str(f))  # reads from disk (chunked)

        tracker_mem = FileContextTracker()
        tracker_mem.record_read(str(f), content=content)  # in-memory hash

        abs_path = os.path.abspath(str(f))
        hash_disk = tracker_disk._tracked[abs_path].content_hash
        hash_mem = tracker_mem._tracked[abs_path].content_hash
        assert hash_disk == hash_mem

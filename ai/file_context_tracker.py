"""
ai/file_context_tracker.py
Track files read by the AI agent and detect external modifications.

When the agent reads a file (via read_document or execute_script), we record
the file path and a content hash. Before subsequent references, we can check
if the file was modified externally and warn the agent to re-read.
"""
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TrackedFile:
    """Record of a file the agent has read."""
    path: str
    content_hash: str
    last_read_timestamp: float
    read_count: int = 1


class FileContextTracker:
    """Tracks files read by the agent and detects external modifications."""

    def __init__(self):
        self._tracked: dict[str, TrackedFile] = {}

    @staticmethod
    def _compute_file_hash(file_path: str) -> str:
        """Compute SHA-256 hash of a file using chunked reads.

        Uses 8KB chunks to avoid loading large files (e.g., STL exports,
        3MF files) entirely into memory.

        Returns:
            Hex digest string, or empty string on read failure.
        """
        h = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(8192)  # 8KB chunks
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()
        except (OSError, IOError):
            return ""

    def record_read(self, file_path: str, content: str | bytes | None = None) -> None:
        """Record that the agent read a file.

        Args:
            file_path: Path to the file (will be normalized to absolute)
            content: File content for hashing. If None, reads from disk
                     using chunked I/O to avoid memory pressure on large files.
        """
        abs_path = os.path.abspath(file_path)

        if content is not None:
            if isinstance(content, str):
                content = content.encode("utf-8")
            content_hash = hashlib.sha256(content).hexdigest()
        else:
            content_hash = self._compute_file_hash(abs_path)
            if not content_hash:
                logger.debug("Could not read file for tracking: %s", abs_path)
                return

        if abs_path in self._tracked:
            self._tracked[abs_path].content_hash = content_hash
            self._tracked[abs_path].last_read_timestamp = time.time()
            self._tracked[abs_path].read_count += 1
        else:
            self._tracked[abs_path] = TrackedFile(
                path=abs_path,
                content_hash=content_hash,
                last_read_timestamp=time.time(),
            )

        logger.debug("Tracked file read: %s (hash=%s...)", abs_path, content_hash[:12])

    def check_modified(self, file_path: str) -> bool:
        """Check if a tracked file has been modified since last read.

        Returns True if the file was modified externally, False if unchanged
        or if the file is not tracked.
        """
        abs_path = os.path.abspath(file_path)
        tracked = self._tracked.get(abs_path)

        if tracked is None:
            return False

        current_hash = self._compute_file_hash(abs_path)
        if not current_hash:
            return False

        return current_hash != tracked.content_hash

    def get_stale_files(self) -> list[str]:
        """Return list of tracked files that have been modified externally."""
        stale = []
        for path in self._tracked:
            if self.check_modified(path):
                stale.append(path)
        return stale

    def is_tracked(self, file_path: str) -> bool:
        """Check if a file is being tracked."""
        return os.path.abspath(file_path) in self._tracked

    def get_tracked_files(self) -> list[str]:
        """Return all tracked file paths."""
        return list(self._tracked.keys())

    def untrack(self, file_path: str) -> None:
        """Stop tracking a file."""
        abs_path = os.path.abspath(file_path)
        self._tracked.pop(abs_path, None)

    def reset(self) -> None:
        """Clear all tracked files."""
        self._tracked.clear()

    def to_dict(self) -> dict:
        """Serialize for API/UI consumption."""
        return {
            path: {
                "content_hash": tf.content_hash[:12] + "...",
                "last_read": tf.last_read_timestamp,
                "read_count": tf.read_count,
            }
            for path, tf in self._tracked.items()
        }

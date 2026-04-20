"""
mcp/file_tools.py
File operation tools for the AI agent.

Provides apply_diff (search/replace in files) and write_file (create/overwrite)
tools. Both check file protection and ignore patterns before executing.
"""
import logging
import os

logger = logging.getLogger(__name__)


def apply_diff(file_path: str, search: str, replace: str,
               project_root: str | None = None) -> dict:
    """Apply a search-and-replace diff to a file.

    Args:
        file_path: Path to the file (relative to project root)
        search: Exact text to search for
        replace: Text to replace it with
        project_root: Project root directory (defaults to cwd)

    Returns:
        Dict with success status and details
    """
    root = project_root or os.getcwd()
    abs_path = os.path.normpath(os.path.join(root, file_path))

    # Security: ensure path is within project root
    if not abs_path.startswith(os.path.normpath(root)):
        return {"success": False, "error": "Path traversal detected"}

    # Check file protection
    from ai.protected_controller import get_protected_controller
    if get_protected_controller().is_protected(abs_path):
        return {
            "success": False,
            "error": f"File '{file_path}' is write-protected. Requires explicit user confirmation.",
            "protected": True,
        }

    # Check ignore patterns
    from ai.ignore_controller import get_ignore_controller
    if get_ignore_controller().is_blocked(abs_path):
        return {"success": False, "error": f"File '{file_path}' is blocked by access controls."}

    if not os.path.exists(abs_path):
        return {"success": False, "error": f"File not found: {file_path}"}

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as exc:
        return {"success": False, "error": f"Cannot read file: {exc}"}

    if search not in content:
        return {
            "success": False,
            "error": "Search text not found in file",
            "file_length": len(content),
        }

    count = content.count(search)
    new_content = content.replace(search, replace, 1)  # Replace first occurrence only

    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except OSError as exc:
        return {"success": False, "error": f"Cannot write file: {exc}"}

    return {
        "success": True,
        "file": file_path,
        "occurrences_found": count,
        "replaced": 1,
        "chars_removed": len(search),
        "chars_added": len(replace),
    }


def write_file(file_path: str, content: str,
               project_root: str | None = None,
               create_dirs: bool = True) -> dict:
    """Write content to a file (create or overwrite).

    Args:
        file_path: Path to the file (relative to project root)
        content: Content to write
        project_root: Project root directory
        create_dirs: Whether to create parent directories

    Returns:
        Dict with success status and details
    """
    root = project_root or os.getcwd()
    abs_path = os.path.normpath(os.path.join(root, file_path))

    # Security: ensure path is within project root
    if not abs_path.startswith(os.path.normpath(root)):
        return {"success": False, "error": "Path traversal detected"}

    # Check file protection
    from ai.protected_controller import get_protected_controller
    if get_protected_controller().is_protected(abs_path):
        return {
            "success": False,
            "error": f"File '{file_path}' is write-protected.",
            "protected": True,
        }

    is_new = not os.path.exists(abs_path)

    try:
        if create_dirs:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        return {"success": False, "error": f"Cannot write file: {exc}"}

    return {
        "success": True,
        "file": file_path,
        "created": is_new,
        "bytes_written": len(content.encode("utf-8")),
    }

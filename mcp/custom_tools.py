"""
mcp/custom_tools.py
Dynamic custom tools registry -- create, test, save, and manage custom tools.

Custom tools are Python scripts that run in the existing execute_script sandbox.
They are defined with a name, description, JSON schema parameters, and a Python
script body. Saved tools persist to data/custom_tools/ and load at startup.
"""
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger(__name__)

CUSTOM_TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "custom_tools",
)
INDEX_FILE = os.path.join(CUSTOM_TOOLS_DIR, "_index.json")


@dataclass
class CustomToolDefinition:
    """Definition of a user-created custom tool."""
    name: str                           # Must start with "custom_"
    description: str
    parameters: dict                    # JSON schema for input parameters
    script: str                         # Python script body
    group: str = "custom_tools"         # Tool group for mode filtering
    author: str = "agent"               # "agent" or "user"
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    def validate_name(self) -> str | None:
        """Validate the tool name. Returns error message or None."""
        if not self.name:
            return "Tool name is required"
        if not self.name.startswith("custom_"):
            return "Custom tool names must start with 'custom_'"
        if not re.match(r'^custom_[a-z][a-z0-9_]*$', self.name):
            return "Tool name must be lowercase alphanumeric with underscores (e.g., custom_my_tool)"
        if len(self.name) > 64:
            return "Tool name must be 64 characters or less"
        return None

    def to_tool_definition(self) -> dict:
        """Convert to MCP tool definition format."""
        defn = {
            "name": self.name,
            "description": self.description,
        }
        if self.parameters:
            defn["input_schema"] = {
                "type": "object",
                **self.parameters,
            }
        return defn

    def to_dict(self) -> dict:
        """Serialize for storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CustomToolDefinition":
        """Deserialize from storage."""
        # Filter to only known fields
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# Forbidden patterns in custom tool scripts (security)
_FORBIDDEN_PATTERNS = [
    r'\b__import__\b',
    r'\bimport\s+os\b',
    r'\bimport\s+sys\b',
    r'\bimport\s+subprocess\b',
    r'\bopen\s*\(',
    r'\bexec\s*\(',
    r'\beval\s*\(',
    r'\bcompile\s*\(',
    r'\bgetattr\s*\(\s*__builtins__',
]


def validate_script(script: str) -> list[str]:
    """Static analysis of a custom tool script for forbidden patterns.

    Returns list of warning messages (empty = safe).
    """
    warnings = []
    for pattern in _FORBIDDEN_PATTERNS:
        if re.search(pattern, script):
            warnings.append(f"Forbidden pattern detected: {pattern}")
    return warnings


class CustomToolRegistry:
    """Manages the lifecycle of custom tools: create, test, save, load, delete."""

    def __init__(self, tools_dir: str | None = None):
        self._tools_dir = tools_dir or CUSTOM_TOOLS_DIR
        self._index_file = os.path.join(self._tools_dir, "_index.json")
        self._saved: dict[str, CustomToolDefinition] = {}   # Persisted tools
        self._drafts: dict[str, CustomToolDefinition] = {}   # In-memory only
        self._lock = threading.RLock()
        self._load_saved()

    def _load_saved(self) -> None:
        """Load saved tools from disk."""
        if not os.path.exists(self._index_file):
            return
        try:
            with open(self._index_file, "r", encoding="utf-8") as f:
                index = json.load(f)
            if not isinstance(index, dict):
                return
            for name, meta in index.items():
                try:
                    # Load script from file
                    script_path = os.path.join(self._tools_dir, name, "script.py")
                    defn_path = os.path.join(self._tools_dir, name, "definition.json")
                    if os.path.exists(defn_path):
                        with open(defn_path, "r", encoding="utf-8") as f:
                            defn_data = json.load(f)
                        if os.path.exists(script_path):
                            with open(script_path, "r", encoding="utf-8") as f:
                                defn_data["script"] = f.read()
                        tool = CustomToolDefinition.from_dict(defn_data)
                        self._saved[name] = tool
                        logger.info("Loaded custom tool: %s", name)
                except Exception as exc:
                    logger.warning("Failed to load custom tool '%s': %s", name, exc)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load custom tools index: %s", exc)

    def _save_index(self) -> None:
        """Save the index file."""
        os.makedirs(self._tools_dir, exist_ok=True)
        index = {}
        for name, tool in self._saved.items():
            index[name] = {
                "description": tool.description,
                "group": tool.group,
                "version": tool.version,
            }
        with open(self._index_file, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)

    # -- CRUD Operations --

    def create_draft(self, name: str, description: str, parameters: dict,
                     script: str, tags: list[str] | None = None) -> dict:
        """Create a draft custom tool (in-memory only, not persisted).

        Returns dict with success/error status.
        """
        tool = CustomToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            script=script,
            tags=tags or [],
        )

        # Validate name
        name_error = tool.validate_name()
        if name_error:
            return {"success": False, "error": name_error}

        with self._lock:
            # Check for collision with saved tools
            if name in self._saved:
                return {"success": False, "error": f"Tool '{name}' already exists as a saved tool. Use edit to modify."}

            # Validate script
            script_warnings = validate_script(script)

            self._drafts[name] = tool

        result = {
            "success": True,
            "name": name,
            "status": "draft",
            "message": f"Draft tool '{name}' created. Use test_custom_tool to test it.",
        }
        if script_warnings:
            result["warnings"] = script_warnings

        return result

    def test_tool(self, name: str, test_params: dict,
                  execute_fn=None) -> dict:
        """Test a draft or saved custom tool with given parameters.

        Args:
            name: Tool name to test
            test_params: Parameters to pass to the tool
            execute_fn: Function to execute scripts (e.g., mcp_server.execute_tool)

        Returns dict with test results.
        """
        with self._lock:
            tool = self._drafts.get(name) or self._saved.get(name)
        if not tool:
            return {"success": False, "error": f"Tool '{name}' not found (no draft or saved tool)"}

        # Build the test script by injecting params as JSON
        # Use repr() to produce a safe Python string literal -- never interpolate
        # raw JSON into triple-quoted strings (TASK-182: prevents ''' breakout injection)
        params_json = json.dumps(test_params)
        safe_params_literal = repr(params_json)
        wrapped_script = f"""# Custom tool test: {name}
import json

# Injected parameters (DO NOT MODIFY)
params = json.loads({safe_params_literal})

# --- Tool script begins ---
{tool.script}
# --- Tool script ends ---
"""

        if execute_fn:
            try:
                result = execute_fn("execute_script", {"script": wrapped_script})
                return {
                    "success": True,
                    "tool_name": name,
                    "test_params": test_params,
                    "execution_result": result,
                }
            except Exception as exc:
                return {
                    "success": False,
                    "tool_name": name,
                    "error": f"Test execution failed: {exc}",
                }
        else:
            # No execution function -- just validate the script can be wrapped
            return {
                "success": True,
                "tool_name": name,
                "test_params": test_params,
                "message": "Script validated (no execution function available for live test)",
                "wrapped_script_length": len(wrapped_script),
            }

    def save_tool(self, name: str) -> dict:
        """Promote a draft tool to saved (persisted to disk).

        Returns dict with success/error status.
        """
        with self._lock:
            tool = self._drafts.get(name)
            if not tool:
                return {"success": False, "error": f"No draft tool named '{name}' to save"}

            # Persist to disk
            tool_dir = os.path.join(self._tools_dir, name)
            try:
                os.makedirs(tool_dir, exist_ok=True)

                # Save definition (without script)
                defn_data = tool.to_dict()
                script = defn_data.pop("script", "")
                with open(os.path.join(tool_dir, "definition.json"), "w", encoding="utf-8") as f:
                    json.dump(defn_data, f, indent=2)

                # Save script separately
                with open(os.path.join(tool_dir, "script.py"), "w", encoding="utf-8") as f:
                    f.write(script)

                # Move from drafts to saved
                self._saved[name] = tool
                del self._drafts[name]

                # Update index
                self._save_index()

                logger.info("Saved custom tool: %s", name)
                return {
                    "success": True,
                    "name": name,
                    "status": "saved",
                    "message": f"Tool '{name}' saved to disk. It will be loaded automatically on next startup.",
                }
            except OSError as exc:
                return {"success": False, "error": f"Failed to save tool: {exc}"}

    def edit_tool(self, name: str, description: str | None = None,
                  parameters: dict | None = None, script: str | None = None,
                  tags: list[str] | None = None) -> dict:
        """Edit an existing saved or draft tool."""
        with self._lock:
            tool = self._saved.get(name) or self._drafts.get(name)
            if not tool:
                return {"success": False, "error": f"Tool '{name}' not found"}

            if description is not None:
                tool.description = description
            if parameters is not None:
                tool.parameters = parameters
            if script is not None:
                tool.script = script
            if tags is not None:
                tool.tags = tags

            # Always increment version and update timestamp when content changes
            tool.updated_at = time.time()
            tool.version += 1

            # Validate script after applying changes
            warnings = validate_script(tool.script) if script else []

            # If it's a saved tool, persist changes
            if name in self._saved:
                self.save_tool_direct(tool)

            result = {"success": True, "name": name, "version": tool.version,
                      "message": f"Tool '{name}' updated to version {tool.version}"}
            if warnings:
                result["warnings"] = warnings
            return result

    def save_tool_direct(self, tool: CustomToolDefinition) -> None:
        """Persist a tool directly (internal helper).

        Note: Caller must already hold ``self._lock`` if needed, or this
        method acquires it (RLock allows re-entrant acquisition).
        """
        with self._lock:
            tool_dir = os.path.join(self._tools_dir, tool.name)
            os.makedirs(tool_dir, exist_ok=True)
            defn_data = tool.to_dict()
            script = defn_data.pop("script", "")
            with open(os.path.join(tool_dir, "definition.json"), "w", encoding="utf-8") as f:
                json.dump(defn_data, f, indent=2)
            with open(os.path.join(tool_dir, "script.py"), "w", encoding="utf-8") as f:
                f.write(script)
            self._saved[tool.name] = tool
            self._save_index()

    def delete_tool(self, name: str) -> dict:
        """Delete a saved or draft tool."""
        import shutil

        with self._lock:
            if name in self._drafts:
                del self._drafts[name]
                return {"success": True, "name": name, "message": f"Draft tool '{name}' deleted"}

            if name in self._saved:
                del self._saved[name]
                # Remove from disk
                tool_dir = os.path.join(self._tools_dir, name)
                if os.path.exists(tool_dir):
                    shutil.rmtree(tool_dir)
                self._save_index()
                return {"success": True, "name": name, "message": f"Saved tool '{name}' deleted"}

            return {"success": False, "error": f"Tool '{name}' not found"}

    def list_tools(self) -> dict:
        """List all custom tools (saved and draft)."""
        with self._lock:
            tools = []
            for name, tool in self._saved.items():
                tools.append({
                    "name": name,
                    "description": tool.description,
                    "status": "saved",
                    "version": tool.version,
                    "group": tool.group,
                    "tags": tool.tags,
                })
            for name, tool in self._drafts.items():
                tools.append({
                    "name": name,
                    "description": tool.description,
                    "status": "draft",
                    "version": tool.version,
                    "group": tool.group,
                    "tags": tool.tags,
                })
            return {"success": True, "tools": tools, "count": len(tools)}

    def get_tool(self, name: str) -> CustomToolDefinition | None:
        """Get a tool by name."""
        with self._lock:
            return self._saved.get(name) or self._drafts.get(name)

    def get_saved_tools(self) -> list[CustomToolDefinition]:
        """Return all saved tools."""
        with self._lock:
            return list(self._saved.values())

    def get_tool_definitions(self) -> list[dict]:
        """Get MCP tool definitions for all saved custom tools."""
        with self._lock:
            return [tool.to_tool_definition() for tool in self._saved.values()]

    def execute_custom_tool(self, name: str, args: dict,
                            execute_fn=None) -> dict:
        """Execute a saved custom tool.

        Wraps the tool's script with parameter injection and delegates
        to execute_script via the execute_fn.
        """
        with self._lock:
            tool = self._saved.get(name)
        if not tool:
            return {"success": False, "error": f"Custom tool '{name}' not found"}

        # Use repr() to produce a safe Python string literal -- never interpolate
        # raw JSON into triple-quoted strings (TASK-182: prevents ''' breakout injection)
        params_json = json.dumps(args)
        safe_params_literal = repr(params_json)
        wrapped_script = f"""# Custom tool: {name} v{tool.version}
import json

# Injected parameters
params = json.loads({safe_params_literal})

# --- Tool implementation ---
{tool.script}
"""

        if execute_fn:
            try:
                result = execute_fn("execute_script", {"script": wrapped_script})
                return {
                    "success": True,
                    "tool_name": name,
                    "result": result,
                }
            except Exception as exc:
                return {"success": False, "error": str(exc)}

        return {"success": False, "error": "No execution function available"}

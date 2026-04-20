"""
mcp/base_tool.py
Abstract base class for MCP tools with common patterns.

Provides input validation, error handling, logging, and result formatting
to reduce boilerplate across tool handlers.
"""
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from mcp.tool_validator import validate_tool_input, ToolValidationResult

logger = logging.getLogger(__name__)


class ToolResult:
    """Standardized tool execution result."""
    def __init__(self, success: bool, data: dict | None = None,
                 error: str | None = None, duration: float = 0.0):
        self.success = success
        self.data = data or {}
        self.error = error
        self.duration = duration

    def to_dict(self) -> dict:
        result = {"success": self.success, **self.data}
        if self.error:
            result["error"] = self.error
        if self.duration > 0:
            result["duration_ms"] = round(self.duration * 1000, 1)
        return result


class BaseTool(ABC):
    """Abstract base class for MCP tools.

    Subclasses implement ``execute()`` with their logic. The base class
    handles input validation, error wrapping, timing, and logging.

    Usage::

        class MyTool(BaseTool):
            name = "my_tool"
            description = "Does something"
            schema = {"properties": {...}, "required": [...]}

            def execute(self, args: dict) -> dict:
                return {"result": "done"}
    """

    name: str = ""
    description: str = ""
    schema: dict | None = None

    def validate(self, args: dict) -> ToolValidationResult:
        """Validate input arguments against the tool's schema."""
        return validate_tool_input(self.name, args, self.schema)

    @abstractmethod
    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool with validated arguments.

        Returns a dict that will be merged into the ToolResult.
        Raise exceptions for errors -- they will be caught and formatted.
        """
        ...

    def run(self, args: dict[str, Any]) -> ToolResult:
        """Full execution pipeline: validate -> execute -> format.

        This is the main entry point. It handles:
        1. Input validation against schema
        2. Timing the execution
        3. Error catching and formatting
        4. Logging
        """
        # Validate
        validation = self.validate(args)
        if not validation.is_valid:
            error_msgs = "; ".join(f"{e.field}: {e.message}" for e in validation.errors)
            # TASK-213: Use logger.error outside except blocks (not .exception)
            logger.error("Tool '%s' validation failed: %s", self.name, error_msgs)
            return ToolResult(
                success=False,
                error=f"Input validation failed: {error_msgs}",
            )

        # Execute with timing
        start = time.monotonic()
        try:
            result_data = self.execute(args)
            duration = time.monotonic() - start
            logger.debug("Tool '%s' executed in %.1fms", self.name, duration * 1000)
            return ToolResult(success=True, data=result_data, duration=duration)
        except Exception as exc:
            duration = time.monotonic() - start
            # logger.exception is correct here -- inside except block
            logger.exception("Tool '%s' failed after %.1fms", self.name, duration * 1000)
            return ToolResult(success=False, error=str(exc), duration=duration)

    def to_definition(self) -> dict:
        """Generate MCP tool definition dict from this tool.

        TASK-212: Always includes ``input_schema``, defaulting to an
        empty object schema when the tool declares no ``schema``.
        """
        defn = {
            "name": self.name,
            "description": self.description,
        }
        if self.schema:
            defn["input_schema"] = {
                "type": "object",
                **self.schema,
            }
        else:
            defn["input_schema"] = {"type": "object", "properties": {}}
        return defn

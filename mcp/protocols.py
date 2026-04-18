"""Protocol definitions for MCP server interfaces."""
from typing import Protocol, Any, runtime_checkable


@runtime_checkable
class MCPServerProtocol(Protocol):
    """Protocol defining what an MCP server must implement."""

    def execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Execute a named tool with the given input."""
        ...

    def get_available_tools(self, groups: list[str] | None = None) -> list[dict[str, Any]]:
        """Return tool definitions, optionally filtered by groups."""
        ...

    def register_post_hook(self, hook: Any) -> None:
        """Register a post-execution hook."""
        ...

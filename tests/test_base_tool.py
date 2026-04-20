"""
tests/test_base_tool.py
Unit tests for mcp/base_tool.py -- structured tool base class.
"""
import pytest

from mcp.base_tool import BaseTool, ToolResult


# ---------------------------------------------------------------------------
# Concrete tool subclass for testing
# ---------------------------------------------------------------------------

class EchoTool(BaseTool):
    """A simple tool that echoes its input for testing."""
    name = "echo"
    description = "Echoes input back"
    schema = {
        "properties": {
            "message": {"type": "string"},
            "count": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["message"],
    }

    def execute(self, args):
        return {"echoed": args["message"], "count": args.get("count", 1)}


class FailingTool(BaseTool):
    """A tool that always raises an exception."""
    name = "fail_tool"
    description = "Always fails"
    schema = {
        "properties": {
            "reason": {"type": "string"},
        },
        "required": [],
    }

    def execute(self, args):
        raise RuntimeError(args.get("reason", "intentional failure"))


class NoSchemaTool(BaseTool):
    """A tool with no schema -- accepts anything."""
    name = "no_schema"
    description = "No schema tool"
    schema = None

    def execute(self, args):
        return {"received": args}


# ---------------------------------------------------------------------------
# Successful execution
# ---------------------------------------------------------------------------

class TestSuccessfulExecution:
    """Concrete tool subclass executes successfully."""

    def test_run_returns_success(self):
        tool = EchoTool()
        result = tool.run({"message": "hello"})
        assert result.success is True
        assert result.data["echoed"] == "hello"
        assert result.data["count"] == 1

    def test_run_with_all_args(self):
        tool = EchoTool()
        result = tool.run({"message": "hi", "count": 5})
        assert result.success is True
        assert result.data["count"] == 5

    def test_no_schema_tool_accepts_anything(self):
        tool = NoSchemaTool()
        result = tool.run({"anything": "goes", "number": 42})
        assert result.success is True
        assert result.data["received"] == {"anything": "goes", "number": 42}


# ---------------------------------------------------------------------------
# Validation failure
# ---------------------------------------------------------------------------

class TestValidationFailure:
    """Validation failure returns error result."""

    def test_missing_required_field(self):
        tool = EchoTool()
        result = tool.run({})  # missing 'message'
        assert result.success is False
        assert "validation failed" in result.error.lower()
        assert "message" in result.error

    def test_wrong_type(self):
        tool = EchoTool()
        result = tool.run({"message": 42})  # should be string
        assert result.success is False
        assert "validation failed" in result.error.lower()

    def test_out_of_range(self):
        tool = EchoTool()
        result = tool.run({"message": "hello", "count": 200})
        assert result.success is False
        assert "validation failed" in result.error.lower()


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------

class TestExceptionHandling:
    """Exception in execute is caught and formatted."""

    def test_exception_returns_error_result(self):
        tool = FailingTool()
        result = tool.run({"reason": "test failure"})
        assert result.success is False
        assert result.error == "test failure"

    def test_exception_has_duration(self):
        tool = FailingTool()
        result = tool.run({})
        assert result.success is False
        assert result.duration >= 0


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

class TestTiming:
    """Timing is recorded in results."""

    def test_duration_is_positive(self):
        tool = EchoTool()
        result = tool.run({"message": "hello"})
        assert result.duration >= 0

    def test_duration_in_to_dict(self):
        tool = EchoTool()
        result = tool.run({"message": "hello"})
        d = result.to_dict()
        assert "duration_ms" in d
        assert d["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# to_definition()
# ---------------------------------------------------------------------------

class TestToDefinition:
    """to_definition() generates correct dict."""

    def test_definition_with_schema(self):
        tool = EchoTool()
        defn = tool.to_definition()
        assert defn["name"] == "echo"
        assert defn["description"] == "Echoes input back"
        assert "input_schema" in defn
        assert defn["input_schema"]["type"] == "object"
        assert "properties" in defn["input_schema"]
        assert "required" in defn["input_schema"]

    def test_definition_without_schema(self):
        """TASK-212: Schema-less tools still include a default input_schema."""
        tool = NoSchemaTool()
        defn = tool.to_definition()
        assert defn["name"] == "no_schema"
        assert defn["description"] == "No schema tool"
        assert "input_schema" in defn
        assert defn["input_schema"] == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# ToolResult.to_dict()
# ---------------------------------------------------------------------------

class TestToolResultToDict:
    """ToolResult.to_dict() serialization."""

    def test_success_result(self):
        result = ToolResult(success=True, data={"key": "value"}, duration=0.05)
        d = result.to_dict()
        assert d["success"] is True
        assert d["key"] == "value"
        assert d["duration_ms"] == 50.0
        assert "error" not in d

    def test_error_result(self):
        result = ToolResult(success=False, error="something broke", duration=0.01)
        d = result.to_dict()
        assert d["success"] is False
        assert d["error"] == "something broke"
        assert d["duration_ms"] == 10.0

    def test_no_duration_omitted(self):
        result = ToolResult(success=True, data={"a": 1})
        d = result.to_dict()
        assert "duration_ms" not in d

    def test_empty_data(self):
        result = ToolResult(success=True)
        d = result.to_dict()
        assert d == {"success": True}


# ---------------------------------------------------------------------------
# TASK-212: to_definition always includes input_schema
# ---------------------------------------------------------------------------

class TestToDefinitionAlwaysHasInputSchema:
    """TASK-212: to_definition() must always include input_schema."""

    def test_no_schema_tool_has_default_input_schema(self):
        tool = NoSchemaTool()
        defn = tool.to_definition()
        assert "input_schema" in defn
        assert defn["input_schema"]["type"] == "object"
        assert defn["input_schema"]["properties"] == {}

    def test_schema_tool_has_populated_input_schema(self):
        tool = EchoTool()
        defn = tool.to_definition()
        assert "input_schema" in defn
        assert "message" in defn["input_schema"]["properties"]


# ---------------------------------------------------------------------------
# TASK-213: logger.error outside except blocks
# ---------------------------------------------------------------------------

class TestLoggerUsage:
    """TASK-213: Validation failure must use logger.error, not logger.exception."""

    def test_validation_failure_log_has_no_nonetype_traceback(self, caplog):
        """Validation path uses logger.error so no NoneType traceback appears."""
        import logging
        tool = EchoTool()
        with caplog.at_level(logging.WARNING):
            result = tool.run({})  # missing required 'message'
        assert result.success is False
        # logger.exception outside except would produce "NoneType: None"
        assert "NoneType: None" not in caplog.text

    def test_exception_path_still_logs_traceback(self, caplog):
        """Exception in execute() uses logger.exception (inside except block)."""
        import logging
        tool = FailingTool()
        with caplog.at_level(logging.ERROR):
            result = tool.run({"reason": "boom"})
        assert result.success is False
        # logger.exception inside except block should include traceback
        assert "boom" in caplog.text

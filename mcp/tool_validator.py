"""
mcp/tool_validator.py
JSON Schema validation for MCP tool inputs.

Validates tool arguments against their schema definitions before dispatch,
catching type mismatches, missing required fields, and invalid values.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ToolValidationError:
    """A single validation error."""
    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message

    def __repr__(self):
        return f"ToolValidationError(field={self.field!r}, message={self.message!r})"


class ToolValidationResult:
    """Result of validating tool inputs."""
    def __init__(self, tool_name: str, errors: list[ToolValidationError] | None = None):
        self.tool_name = tool_name
        self.errors = errors or []

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "is_valid": self.is_valid,
            "errors": [{"field": e.field, "message": e.message} for e in self.errors],
        }


def validate_tool_input(tool_name: str, args: dict[str, Any],
                         schema: dict | None = None) -> ToolValidationResult:
    """Validate tool arguments against a JSON schema.

    If no schema is provided, only basic type checking is performed.

    Args:
        tool_name: Name of the tool being validated
        args: The arguments dict to validate
        schema: JSON schema dict with 'properties' and 'required' keys

    Returns:
        ToolValidationResult with any errors found
    """
    errors = []

    if not isinstance(args, dict):
        errors.append(ToolValidationError("_root", f"Expected dict, got {type(args).__name__}"))
        return ToolValidationResult(tool_name, errors)

    if schema is None:
        return ToolValidationResult(tool_name)

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    # Check required fields
    for field_name in required:
        if field_name not in args:
            errors.append(ToolValidationError(field_name, f"Required field '{field_name}' is missing"))

    # Check types for provided fields
    for field_name, value in args.items():
        if field_name not in properties:
            # Unknown field -- warn but don't reject
            logger.debug("Tool '%s': unknown field '%s'", tool_name, field_name)
            continue

        prop_schema = properties[field_name]
        expected_type = prop_schema.get("type")

        if expected_type and value is not None:
            if not _validate_type(value, expected_type):
                errors.append(ToolValidationError(
                    field_name,
                    f"Expected type '{expected_type}', got '{type(value).__name__}'"
                ))

            # Recursive validation for nested objects (TASK-197)
            if expected_type == "object" and isinstance(value, dict) and "properties" in prop_schema:
                nested_errors = _validate_nested(value, prop_schema)
                for nested_err in nested_errors:
                    errors.append(ToolValidationError(
                        f"{field_name}.{nested_err.field}",
                        nested_err.message,
                    ))

            # Array item validation (TASK-197)
            if expected_type == "array" and isinstance(value, list) and "items" in prop_schema:
                item_schema = prop_schema["items"]
                for i, item in enumerate(value):
                    if item_schema.get("type") == "object" and isinstance(item, dict) and "properties" in item_schema:
                        nested_errors = _validate_nested(item, item_schema)
                        for nested_err in nested_errors:
                            errors.append(ToolValidationError(
                                f"{field_name}[{i}].{nested_err.field}",
                                nested_err.message,
                            ))

        # Check enum values
        enum_values = prop_schema.get("enum")
        if enum_values and value not in enum_values:
            errors.append(ToolValidationError(
                field_name,
                f"Value '{value}' not in allowed values: {enum_values}"
            ))

        # Check numeric ranges
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = prop_schema.get("minimum")
            maximum = prop_schema.get("maximum")
            if minimum is not None and value < minimum:
                errors.append(ToolValidationError(
                    field_name, f"Value {value} is below minimum {minimum}"
                ))
            if maximum is not None and value > maximum:
                errors.append(ToolValidationError(
                    field_name, f"Value {value} exceeds maximum {maximum}"
                ))

    return ToolValidationResult(tool_name, errors)


def _validate_type(value: Any, expected_type: str) -> bool:
    """Check whether *value* matches *expected_type* (JSON Schema type name).

    TASK-196: ``bool`` is explicitly excluded from ``int``/``float`` checks
    because Python's ``bool`` is a subclass of ``int``.
    """
    if expected_type == "integer":
        if isinstance(value, bool):
            return False
        return isinstance(value, int)
    elif expected_type == "number":
        if isinstance(value, bool):
            return False
        return isinstance(value, (int, float))
    elif expected_type == "boolean":
        return isinstance(value, bool)
    elif expected_type == "string":
        return isinstance(value, str)
    elif expected_type == "array":
        return isinstance(value, list)
    elif expected_type == "object":
        return isinstance(value, dict)
    return True  # unknown type -- pass


def _validate_nested(data: dict, schema: dict) -> list[ToolValidationError]:
    """Recursively validate a nested object against its schema.

    TASK-197: Returns a flat list of :class:`ToolValidationError` with
    field names relative to *data* (the caller prefixes the parent path).
    """
    errors: list[ToolValidationError] = []
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    # Check required fields
    for field_name in required:
        if field_name not in data:
            errors.append(ToolValidationError(field_name, f"Required field '{field_name}' is missing"))

    # Validate each provided field
    for field_name, value in data.items():
        if field_name not in properties:
            continue
        prop_schema = properties[field_name]
        expected_type = prop_schema.get("type")
        if expected_type and value is not None:
            if not _validate_type(value, expected_type):
                errors.append(ToolValidationError(
                    field_name,
                    f"Expected type '{expected_type}', got '{type(value).__name__}'"
                ))
            # Recurse into nested objects
            if expected_type == "object" and isinstance(value, dict) and "properties" in prop_schema:
                nested = _validate_nested(value, prop_schema)
                for ne in nested:
                    errors.append(ToolValidationError(f"{field_name}.{ne.field}", ne.message))
            # Recurse into array items
            if expected_type == "array" and isinstance(value, list) and "items" in prop_schema:
                item_schema = prop_schema["items"]
                for i, item in enumerate(value):
                    if item_schema.get("type") == "object" and isinstance(item, dict) and "properties" in item_schema:
                        nested = _validate_nested(item, item_schema)
                        for ne in nested:
                            errors.append(ToolValidationError(f"{field_name}[{i}].{ne.field}", ne.message))
    return errors

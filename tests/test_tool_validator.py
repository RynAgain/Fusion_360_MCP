"""
tests/test_tool_validator.py
Unit tests for mcp/tool_validator.py -- JSON schema validation for MCP tool inputs.
"""
import logging
import pytest

from mcp.tool_validator import (
    ToolValidationError,
    ToolValidationResult,
    validate_tool_input,
)


# ---------------------------------------------------------------------------
# Sample schemas for testing
# ---------------------------------------------------------------------------

CYLINDER_SCHEMA = {
    "properties": {
        "diameter": {"type": "number", "minimum": 0.1, "maximum": 1000},
        "height": {"type": "number", "minimum": 0.1},
        "name": {"type": "string"},
        "material": {"type": "string", "enum": ["steel", "aluminum", "plastic"]},
        "segments": {"type": "integer", "minimum": 3, "maximum": 128},
    },
    "required": ["diameter", "height"],
}


# ---------------------------------------------------------------------------
# Valid input
# ---------------------------------------------------------------------------

class TestValidInput:
    """Valid inputs should produce no errors."""

    def test_valid_input_passes(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 5.0, "height": 10.0, "name": "cyl1"},
            CYLINDER_SCHEMA,
        )
        assert result.is_valid
        assert result.errors == []
        assert result.tool_name == "create_cylinder"

    def test_valid_input_with_all_fields(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 5.0, "height": 10.0, "name": "cyl1",
             "material": "steel", "segments": 32},
            CYLINDER_SCHEMA,
        )
        assert result.is_valid

    def test_valid_enum_value(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 5.0, "height": 10.0, "material": "aluminum"},
            CYLINDER_SCHEMA,
        )
        assert result.is_valid


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

class TestMissingRequired:
    """Missing required fields should produce errors."""

    def test_missing_required_field(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 5.0},  # missing 'height'
            CYLINDER_SCHEMA,
        )
        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].field == "height"
        assert "Required field" in result.errors[0].message

    def test_missing_all_required_fields(self):
        result = validate_tool_input(
            "create_cylinder",
            {"name": "cyl1"},
            CYLINDER_SCHEMA,
        )
        assert not result.is_valid
        assert len(result.errors) == 2
        fields = {e.field for e in result.errors}
        assert fields == {"diameter", "height"}


# ---------------------------------------------------------------------------
# Wrong types
# ---------------------------------------------------------------------------

class TestWrongType:
    """Type mismatches should produce errors."""

    def test_string_instead_of_number(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": "five", "height": 10.0},
            CYLINDER_SCHEMA,
        )
        assert not result.is_valid
        assert result.errors[0].field == "diameter"
        assert "Expected type 'number'" in result.errors[0].message

    def test_float_instead_of_integer(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 5.0, "height": 10.0, "segments": 32.5},
            CYLINDER_SCHEMA,
        )
        assert not result.is_valid
        assert result.errors[0].field == "segments"
        assert "Expected type 'integer'" in result.errors[0].message

    def test_integer_instead_of_string(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 5.0, "height": 10.0, "name": 42},
            CYLINDER_SCHEMA,
        )
        assert not result.is_valid
        assert result.errors[0].field == "name"
        assert "Expected type 'string'" in result.errors[0].message

    def test_none_value_skips_type_check(self):
        """None values should not trigger type errors."""
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 5.0, "height": 10.0, "name": None},
            CYLINDER_SCHEMA,
        )
        assert result.is_valid


# ---------------------------------------------------------------------------
# Enum validation
# ---------------------------------------------------------------------------

class TestEnumValidation:
    """Enum values should be validated against allowed list."""

    def test_invalid_enum_value(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 5.0, "height": 10.0, "material": "titanium"},
            CYLINDER_SCHEMA,
        )
        assert not result.is_valid
        assert result.errors[0].field == "material"
        assert "not in allowed values" in result.errors[0].message

    def test_valid_enum_value(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 5.0, "height": 10.0, "material": "plastic"},
            CYLINDER_SCHEMA,
        )
        assert result.is_valid


# ---------------------------------------------------------------------------
# Numeric range validation
# ---------------------------------------------------------------------------

class TestNumericRange:
    """Numeric values should be validated against minimum/maximum."""

    def test_value_below_minimum(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 0.01, "height": 10.0},
            CYLINDER_SCHEMA,
        )
        assert not result.is_valid
        assert result.errors[0].field == "diameter"
        assert "below minimum" in result.errors[0].message

    def test_value_above_maximum(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 2000, "height": 10.0},
            CYLINDER_SCHEMA,
        )
        assert not result.is_valid
        assert result.errors[0].field == "diameter"
        assert "exceeds maximum" in result.errors[0].message

    def test_value_at_boundary(self):
        """Values exactly at min/max should pass."""
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 0.1, "height": 0.1},
            CYLINDER_SCHEMA,
        )
        assert result.is_valid

    def test_integer_range(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 5.0, "height": 10.0, "segments": 200},
            CYLINDER_SCHEMA,
        )
        assert not result.is_valid
        assert result.errors[0].field == "segments"
        assert "exceeds maximum" in result.errors[0].message


# ---------------------------------------------------------------------------
# Unknown fields
# ---------------------------------------------------------------------------

class TestUnknownFields:
    """Unknown fields should be allowed (just logged)."""

    def test_unknown_fields_allowed(self, caplog):
        with caplog.at_level(logging.DEBUG):
            result = validate_tool_input(
                "create_cylinder",
                {"diameter": 5.0, "height": 10.0, "color": "red"},
                CYLINDER_SCHEMA,
            )
        assert result.is_valid
        assert "unknown field 'color'" in caplog.text


# ---------------------------------------------------------------------------
# Non-dict input
# ---------------------------------------------------------------------------

class TestNonDictInput:
    """Non-dict args should produce a root-level error."""

    def test_list_input(self):
        result = validate_tool_input("my_tool", [1, 2, 3], CYLINDER_SCHEMA)
        assert not result.is_valid
        assert result.errors[0].field == "_root"
        assert "Expected dict" in result.errors[0].message

    def test_string_input(self):
        result = validate_tool_input("my_tool", "not a dict", CYLINDER_SCHEMA)
        assert not result.is_valid
        assert result.errors[0].field == "_root"

    def test_none_input(self):
        result = validate_tool_input("my_tool", None, CYLINDER_SCHEMA)
        assert not result.is_valid
        assert result.errors[0].field == "_root"


# ---------------------------------------------------------------------------
# No schema = always valid
# ---------------------------------------------------------------------------

class TestNoSchema:
    """When no schema is provided, any dict input is valid."""

    def test_no_schema_always_valid(self):
        result = validate_tool_input("my_tool", {"anything": "goes"})
        assert result.is_valid

    def test_no_schema_empty_dict(self):
        result = validate_tool_input("my_tool", {})
        assert result.is_valid


# ---------------------------------------------------------------------------
# to_dict() serialization
# ---------------------------------------------------------------------------

class TestToDict:
    """Verify to_dict() serialization format."""

    def test_valid_result_to_dict(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": 5.0, "height": 10.0},
            CYLINDER_SCHEMA,
        )
        d = result.to_dict()
        assert d["tool_name"] == "create_cylinder"
        assert d["is_valid"] is True
        assert d["errors"] == []

    def test_invalid_result_to_dict(self):
        result = validate_tool_input(
            "create_cylinder",
            {"diameter": "bad"},
            CYLINDER_SCHEMA,
        )
        d = result.to_dict()
        assert d["tool_name"] == "create_cylinder"
        assert d["is_valid"] is False
        assert len(d["errors"]) > 0
        assert "field" in d["errors"][0]
        assert "message" in d["errors"][0]


# ---------------------------------------------------------------------------
# ToolValidationError repr
# ---------------------------------------------------------------------------

class TestToolValidationError:
    """Verify ToolValidationError repr."""

    def test_repr(self):
        err = ToolValidationError("name", "is required")
        assert "name" in repr(err)
        assert "is required" in repr(err)


# ---------------------------------------------------------------------------
# TASK-196: Boolean / integer type confusion
# ---------------------------------------------------------------------------

class TestBoolIntTypeConfusion:
    """TASK-196: bool must not pass integer or number validation."""

    def test_true_fails_integer_validation(self):
        schema = {"properties": {"val": {"type": "integer"}}, "required": ["val"]}
        result = validate_tool_input("test", {"val": True}, schema)
        assert not result.is_valid
        assert result.errors[0].field == "val"
        assert "Expected type 'integer'" in result.errors[0].message

    def test_true_passes_boolean_validation(self):
        schema = {"properties": {"flag": {"type": "boolean"}}, "required": ["flag"]}
        result = validate_tool_input("test", {"flag": True}, schema)
        assert result.is_valid

    def test_int_passes_integer_validation(self):
        schema = {"properties": {"val": {"type": "integer"}}, "required": ["val"]}
        result = validate_tool_input("test", {"val": 1}, schema)
        assert result.is_valid

    def test_false_fails_number_validation(self):
        schema = {"properties": {"val": {"type": "number"}}, "required": ["val"]}
        result = validate_tool_input("test", {"val": False}, schema)
        assert not result.is_valid
        assert result.errors[0].field == "val"
        assert "Expected type 'number'" in result.errors[0].message


# ---------------------------------------------------------------------------
# TASK-197: Nested schema validation
# ---------------------------------------------------------------------------

NESTED_SCHEMA = {
    "properties": {
        "name": {"type": "string"},
        "address": {
            "type": "object",
            "properties": {
                "street": {"type": "string"},
                "zip": {"type": "integer"},
            },
            "required": ["street"],
        },
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key"],
            },
        },
    },
    "required": ["name"],
}


class TestNestedSchemaValidation:
    """TASK-197: Recursive validation of nested objects and arrays."""

    def test_valid_nested_object_passes(self):
        result = validate_tool_input(
            "test",
            {"name": "Alice", "address": {"street": "123 Main St", "zip": 12345}},
            NESTED_SCHEMA,
        )
        assert result.is_valid

    def test_invalid_nested_field_type_produces_prefixed_error(self):
        result = validate_tool_input(
            "test",
            {"name": "Alice", "address": {"street": "123 Main St", "zip": "not-an-int"}},
            NESTED_SCHEMA,
        )
        assert not result.is_valid
        error_fields = [e.field for e in result.errors]
        assert any("address" in f and "zip" in f for f in error_fields)

    def test_missing_required_nested_field_produces_prefixed_error(self):
        result = validate_tool_input(
            "test",
            {"name": "Alice", "address": {"zip": 12345}},  # missing 'street'
            NESTED_SCHEMA,
        )
        assert not result.is_valid
        error_fields = [e.field for e in result.errors]
        assert any("address" in f and "street" in f for f in error_fields)

    def test_array_of_objects_with_invalid_items(self):
        result = validate_tool_input(
            "test",
            {
                "name": "Alice",
                "tags": [
                    {"key": "color", "value": "red"},
                    {"key": 42, "value": "bad"},      # key should be string
                    {"value": "missing_key"},           # missing required 'key'
                ],
            },
            NESTED_SCHEMA,
        )
        assert not result.is_valid
        error_fields = [e.field for e in result.errors]
        # Should have error for tags[1].key type and tags[2].key missing
        assert any("tags[1]" in f and "key" in f for f in error_fields)
        assert any("tags[2]" in f and "key" in f for f in error_fields)

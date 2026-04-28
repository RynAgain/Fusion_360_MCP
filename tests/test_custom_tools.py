"""
tests/test_custom_tools.py
Comprehensive tests for the custom tools registry (mcp/custom_tools.py).
"""
import json
import os
import pytest

from mcp.custom_tools import (
    CustomToolDefinition,
    CustomToolRegistry,
    validate_script,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry(tmp_path):
    """Create a CustomToolRegistry backed by a temporary directory."""
    return CustomToolRegistry(tools_dir=str(tmp_path))


@pytest.fixture
def sample_tool_kwargs():
    """Return valid kwargs for creating a draft custom tool."""
    return {
        "name": "custom_my_tool",
        "description": "A test tool that doubles a number",
        "parameters": {
            "properties": {
                "value": {"type": "number", "description": "The number to double"},
            },
            "required": ["value"],
        },
        "script": "result = params['value'] * 2",
        "tags": ["test", "math"],
    }


@pytest.fixture
def saved_registry(tmp_path, sample_tool_kwargs):
    """Return a registry with one saved tool already on disk."""
    reg = CustomToolRegistry(tools_dir=str(tmp_path))
    reg.create_draft(**sample_tool_kwargs)
    reg.save_tool("custom_my_tool")
    return reg


# ---------------------------------------------------------------------------
# CustomToolDefinition -- creation and serialization
# ---------------------------------------------------------------------------

class TestCustomToolDefinition:

    def test_create_and_fields(self):
        tool = CustomToolDefinition(
            name="custom_test",
            description="desc",
            parameters={"properties": {}},
            script="result = 1",
        )
        assert tool.name == "custom_test"
        assert tool.description == "desc"
        assert tool.group == "custom_tools"
        assert tool.author == "agent"
        assert tool.version == 1
        assert isinstance(tool.created_at, float)
        assert isinstance(tool.tags, list)

    def test_to_dict_and_from_dict_roundtrip(self):
        tool = CustomToolDefinition(
            name="custom_roundtrip",
            description="roundtrip test",
            parameters={"properties": {"x": {"type": "number"}}, "required": ["x"]},
            script="result = params['x']",
            tags=["a", "b"],
        )
        d = tool.to_dict()
        assert isinstance(d, dict)
        assert d["name"] == "custom_roundtrip"
        assert d["tags"] == ["a", "b"]

        restored = CustomToolDefinition.from_dict(d)
        assert restored.name == tool.name
        assert restored.description == tool.description
        assert restored.parameters == tool.parameters
        assert restored.script == tool.script
        assert restored.tags == tool.tags

    def test_from_dict_ignores_unknown_fields(self):
        data = {
            "name": "custom_extra",
            "description": "d",
            "parameters": {},
            "script": "pass",
            "unknown_field": "should_be_ignored",
        }
        tool = CustomToolDefinition.from_dict(data)
        assert tool.name == "custom_extra"
        assert not hasattr(tool, "unknown_field")

    def test_to_tool_definition_format(self):
        tool = CustomToolDefinition(
            name="custom_fmt",
            description="formatted",
            parameters={"properties": {"x": {"type": "number"}}, "required": ["x"]},
            script="pass",
        )
        defn = tool.to_tool_definition()
        assert defn["name"] == "custom_fmt"
        assert defn["description"] == "formatted"
        assert "input_schema" in defn
        assert defn["input_schema"]["type"] == "object"
        assert "properties" in defn["input_schema"]
        assert "required" in defn["input_schema"]

    def test_to_tool_definition_no_parameters(self):
        tool = CustomToolDefinition(
            name="custom_noparam",
            description="no params",
            parameters={},
            script="result = 42",
        )
        defn = tool.to_tool_definition()
        assert defn["name"] == "custom_noparam"
        assert "input_schema" not in defn


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------

class TestNameValidation:

    def test_valid_name(self):
        tool = CustomToolDefinition(
            name="custom_my_tool", description="d", parameters={}, script="pass"
        )
        assert tool.validate_name() is None

    def test_missing_prefix(self):
        tool = CustomToolDefinition(
            name="my_tool", description="d", parameters={}, script="pass"
        )
        error = tool.validate_name()
        assert error is not None
        assert "custom_" in error

    def test_empty_name(self):
        tool = CustomToolDefinition(
            name="", description="d", parameters={}, script="pass"
        )
        error = tool.validate_name()
        assert error is not None
        assert "required" in error.lower()

    def test_invalid_chars_uppercase(self):
        tool = CustomToolDefinition(
            name="custom_MyTool", description="d", parameters={}, script="pass"
        )
        error = tool.validate_name()
        assert error is not None
        assert "lowercase" in error.lower()

    def test_invalid_chars_dash(self):
        tool = CustomToolDefinition(
            name="custom_my-tool", description="d", parameters={}, script="pass"
        )
        error = tool.validate_name()
        assert error is not None

    def test_name_too_long(self):
        long_name = "custom_" + "a" * 60  # 67 chars > 64
        tool = CustomToolDefinition(
            name=long_name, description="d", parameters={}, script="pass"
        )
        error = tool.validate_name()
        assert error is not None
        assert "64" in error

    def test_name_starts_with_number_after_prefix(self):
        tool = CustomToolDefinition(
            name="custom_1tool", description="d", parameters={}, script="pass"
        )
        error = tool.validate_name()
        assert error is not None


# ---------------------------------------------------------------------------
# validate_script
# ---------------------------------------------------------------------------

class TestValidateScript:

    def test_clean_script_no_warnings(self):
        script = """
x = params['value']
result = x * 2
"""
        warnings = validate_script(script)
        assert warnings == []

    def test_detects_import_os(self):
        warnings = validate_script("import os\nos.listdir('.')")
        assert len(warnings) > 0
        assert any("import\\s+os" in w for w in warnings)

    def test_detects_import_sys(self):
        warnings = validate_script("import sys")
        assert len(warnings) > 0

    def test_detects_import_subprocess(self):
        warnings = validate_script("import subprocess")
        assert len(warnings) > 0

    def test_detects_dunder_import(self):
        warnings = validate_script("__import__('os')")
        assert len(warnings) > 0

    def test_detects_open(self):
        warnings = validate_script("f = open('/etc/passwd')")
        assert len(warnings) > 0

    def test_detects_exec(self):
        warnings = validate_script("exec('print(1)')")
        assert len(warnings) > 0

    def test_detects_eval(self):
        warnings = validate_script("eval('1+1')")
        assert len(warnings) > 0

    def test_detects_compile(self):
        warnings = validate_script("compile('pass', '<string>', 'exec')")
        assert len(warnings) > 0

    def test_allows_json_and_math(self):
        script = """
import json
import math
result = math.sqrt(params['value'])
"""
        warnings = validate_script(script)
        assert warnings == []


# ---------------------------------------------------------------------------
# CustomToolRegistry -- init
# ---------------------------------------------------------------------------

class TestRegistryInit:

    def test_creates_with_empty_state(self, tmp_path):
        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        assert reg._saved == {}
        assert reg._drafts == {}

    def test_uses_provided_dir(self, tmp_path):
        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        assert reg._tools_dir == str(tmp_path)


# ---------------------------------------------------------------------------
# create_draft
# ---------------------------------------------------------------------------

class TestCreateDraft:

    def test_succeeds_with_valid_params(self, registry, sample_tool_kwargs):
        result = registry.create_draft(**sample_tool_kwargs)
        assert result["success"] is True
        assert result["name"] == "custom_my_tool"
        assert result["status"] == "draft"
        assert "custom_my_tool" in registry._drafts

    def test_fails_with_invalid_name(self, registry):
        result = registry.create_draft(
            name="bad_name",
            description="d",
            parameters={},
            script="pass",
        )
        assert result["success"] is False
        assert "custom_" in result["error"]

    def test_fails_with_duplicate_saved_name(self, saved_registry, sample_tool_kwargs):
        result = saved_registry.create_draft(**sample_tool_kwargs)
        assert result["success"] is False
        assert "already exists" in result["error"]

    def test_returns_warnings_for_dangerous_script(self, registry):
        result = registry.create_draft(
            name="custom_danger",
            description="dangerous",
            parameters={},
            script="import os\nos.system('rm -rf /')",
        )
        # Still succeeds (warnings, not errors) but includes warnings
        assert result["success"] is True
        assert "warnings" in result
        assert len(result["warnings"]) > 0


# ---------------------------------------------------------------------------
# test_tool
# ---------------------------------------------------------------------------

class TestTestTool:

    def test_with_mock_execute_fn(self, registry, sample_tool_kwargs):
        registry.create_draft(**sample_tool_kwargs)

        mock_result = {"success": True, "output": "42"}
        def mock_execute(tool_name, args):
            assert tool_name == "execute_script"
            assert "script" in args
            return mock_result

        result = registry.test_tool("custom_my_tool", {"value": 21}, execute_fn=mock_execute)
        assert result["success"] is True
        assert result["tool_name"] == "custom_my_tool"
        assert result["execution_result"] == mock_result

    def test_without_execute_fn(self, registry, sample_tool_kwargs):
        registry.create_draft(**sample_tool_kwargs)
        result = registry.test_tool("custom_my_tool", {"value": 5})
        assert result["success"] is True
        assert "wrapped_script_length" in result
        assert result["wrapped_script_length"] > 0

    def test_nonexistent_tool(self, registry):
        result = registry.test_tool("custom_nonexistent", {})
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_execute_fn_raises(self, registry, sample_tool_kwargs):
        registry.create_draft(**sample_tool_kwargs)

        def failing_execute(tool_name, args):
            raise RuntimeError("Fusion 360 not connected")

        result = registry.test_tool(
            "custom_my_tool", {"value": 1}, execute_fn=failing_execute
        )
        assert result["success"] is False
        assert "Fusion 360 not connected" in result["error"]

    def test_saved_tool_can_be_tested(self, saved_registry):
        result = saved_registry.test_tool("custom_my_tool", {"value": 10})
        assert result["success"] is True


# ---------------------------------------------------------------------------
# save_tool
# ---------------------------------------------------------------------------

class TestSaveTool:

    def test_persists_to_disk(self, registry, sample_tool_kwargs, tmp_path):
        registry.create_draft(**sample_tool_kwargs)
        result = registry.save_tool("custom_my_tool")
        assert result["success"] is True
        assert result["status"] == "saved"

        # Verify files on disk
        tool_dir = os.path.join(str(tmp_path), "custom_my_tool")
        assert os.path.isdir(tool_dir)
        assert os.path.isfile(os.path.join(tool_dir, "definition.json"))
        assert os.path.isfile(os.path.join(tool_dir, "script.py"))

        # Verify index
        index_path = os.path.join(str(tmp_path), "_index.json")
        assert os.path.isfile(index_path)
        with open(index_path, "r") as f:
            index = json.load(f)
        assert "custom_my_tool" in index

    def test_moves_from_drafts_to_saved(self, registry, sample_tool_kwargs):
        registry.create_draft(**sample_tool_kwargs)
        assert "custom_my_tool" in registry._drafts
        assert "custom_my_tool" not in registry._saved

        registry.save_tool("custom_my_tool")
        assert "custom_my_tool" not in registry._drafts
        assert "custom_my_tool" in registry._saved

    def test_fails_for_nonexistent_draft(self, registry):
        result = registry.save_tool("custom_nope")
        assert result["success"] is False
        assert "No draft" in result["error"]


# ---------------------------------------------------------------------------
# Round-trip: save then load in new registry
# ---------------------------------------------------------------------------

class TestRoundTrip:

    def test_saved_tool_loads_on_new_registry_init(self, tmp_path, sample_tool_kwargs):
        # Create and save
        reg1 = CustomToolRegistry(tools_dir=str(tmp_path))
        reg1.create_draft(**sample_tool_kwargs)
        reg1.save_tool("custom_my_tool")

        # New registry from same directory
        reg2 = CustomToolRegistry(tools_dir=str(tmp_path))
        assert "custom_my_tool" in reg2._saved
        tool = reg2._saved["custom_my_tool"]
        assert tool.name == "custom_my_tool"
        assert tool.description == sample_tool_kwargs["description"]
        assert tool.script == sample_tool_kwargs["script"]
        assert tool.parameters == sample_tool_kwargs["parameters"]
        assert tool.tags == sample_tool_kwargs["tags"]


# ---------------------------------------------------------------------------
# edit_tool
# ---------------------------------------------------------------------------

class TestEditTool:

    def test_updates_fields_and_version(self, saved_registry):
        original_version = saved_registry._saved["custom_my_tool"].version
        result = saved_registry.edit_tool(
            "custom_my_tool",
            description="Updated description",
            tags=["updated"],
        )
        assert result["success"] is True
        assert result["version"] == original_version + 1

        tool = saved_registry.get_tool("custom_my_tool")
        assert tool.description == "Updated description"
        assert tool.tags == ["updated"]

    def test_edit_script_with_warning(self, saved_registry):
        result = saved_registry.edit_tool(
            "custom_my_tool",
            script="import os\nresult = 1",
        )
        assert result["success"] is True
        assert "warnings" in result

    def test_edit_script_with_warning_increments_version_and_timestamp(self, saved_registry):
        """TASK-189: version and timestamp must update even when script produces warnings."""
        tool = saved_registry.get_tool("custom_my_tool")
        original_version = tool.version
        original_updated_at = tool.updated_at

        import time
        time.sleep(0.01)  # ensure timestamp difference

        result = saved_registry.edit_tool(
            "custom_my_tool",
            script="import os\nresult = 1",  # triggers a warning
        )
        assert result["success"] is True
        assert "warnings" in result
        assert result["version"] == original_version + 1

        tool = saved_registry.get_tool("custom_my_tool")
        assert tool.version == original_version + 1
        assert tool.updated_at > original_updated_at

    def test_edit_nonexistent_tool(self, registry):
        result = registry.edit_tool("custom_nope", description="nope")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_edit_draft_tool(self, registry, sample_tool_kwargs):
        registry.create_draft(**sample_tool_kwargs)
        result = registry.edit_tool(
            "custom_my_tool",
            description="Draft updated",
        )
        assert result["success"] is True
        tool = registry.get_tool("custom_my_tool")
        assert tool.description == "Draft updated"

    def test_edit_persists_saved_tool(self, tmp_path, sample_tool_kwargs):
        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        reg.create_draft(**sample_tool_kwargs)
        reg.save_tool("custom_my_tool")

        reg.edit_tool("custom_my_tool", description="Persisted edit")

        # Verify by loading fresh
        reg2 = CustomToolRegistry(tools_dir=str(tmp_path))
        tool = reg2.get_tool("custom_my_tool")
        assert tool.description == "Persisted edit"


# ---------------------------------------------------------------------------
# delete_tool
# ---------------------------------------------------------------------------

class TestDeleteTool:

    def test_delete_draft(self, registry, sample_tool_kwargs):
        registry.create_draft(**sample_tool_kwargs)
        result = registry.delete_tool("custom_my_tool")
        assert result["success"] is True
        assert "Draft" in result["message"]
        assert "custom_my_tool" not in registry._drafts

    def test_delete_saved_tool_removes_files(self, saved_registry, tmp_path):
        tool_dir = os.path.join(str(tmp_path), "custom_my_tool")
        assert os.path.isdir(tool_dir)

        result = saved_registry.delete_tool("custom_my_tool")
        assert result["success"] is True
        assert "Saved" in result["message"]
        assert "custom_my_tool" not in saved_registry._saved
        assert not os.path.exists(tool_dir)

    def test_delete_nonexistent(self, registry):
        result = registry.delete_tool("custom_ghost")
        assert result["success"] is False
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# list_tools
# ---------------------------------------------------------------------------

class TestListTools:

    def test_includes_saved_and_draft(self, saved_registry):
        # saved_registry has one saved tool; add a draft
        saved_registry.create_draft(
            name="custom_draft_tool",
            description="draft only",
            parameters={},
            script="pass",
        )
        result = saved_registry.list_tools()
        assert result["success"] is True
        assert result["count"] == 2
        names = [t["name"] for t in result["tools"]]
        assert "custom_my_tool" in names
        assert "custom_draft_tool" in names

        statuses = {t["name"]: t["status"] for t in result["tools"]}
        assert statuses["custom_my_tool"] == "saved"
        assert statuses["custom_draft_tool"] == "draft"

    def test_empty_registry(self, registry):
        result = registry.list_tools()
        assert result["success"] is True
        assert result["count"] == 0
        assert result["tools"] == []


# ---------------------------------------------------------------------------
# get_tool_definitions
# ---------------------------------------------------------------------------

class TestGetToolDefinitions:

    def test_returns_mcp_format(self, saved_registry):
        defns = saved_registry.get_tool_definitions()
        assert len(defns) == 1
        defn = defns[0]
        assert defn["name"] == "custom_my_tool"
        assert "description" in defn
        assert "input_schema" in defn
        assert defn["input_schema"]["type"] == "object"

    def test_empty_registry_returns_empty(self, registry):
        assert registry.get_tool_definitions() == []


# ---------------------------------------------------------------------------
# execute_custom_tool
# ---------------------------------------------------------------------------

class TestExecuteCustomTool:

    def test_wraps_and_calls_execute_fn(self, saved_registry):
        captured = {}
        def mock_execute(tool_name, args):
            captured["tool_name"] = tool_name
            captured["script"] = args["script"]
            return {"success": True, "result": 42}

        result = saved_registry.execute_custom_tool(
            "custom_my_tool", {"value": 21}, execute_fn=mock_execute
        )
        assert result["success"] is True
        assert result["tool_name"] == "custom_my_tool"
        assert captured["tool_name"] == "execute_script"
        assert "params" in captured["script"]
        assert "21" in captured["script"]

    def test_nonexistent_tool(self, registry):
        result = registry.execute_custom_tool("custom_nope", {})
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_no_execute_fn(self, saved_registry):
        result = saved_registry.execute_custom_tool("custom_my_tool", {"value": 1})
        assert result["success"] is False
        assert "No execution function" in result["error"]

    def test_execute_fn_raises(self, saved_registry):
        def failing(tool_name, args):
            raise ValueError("boom")

        result = saved_registry.execute_custom_tool(
            "custom_my_tool", {"value": 1}, execute_fn=failing
        )
        assert result["success"] is False
        assert "boom" in result["error"]


# ---------------------------------------------------------------------------
# get_tool / get_saved_tools
# ---------------------------------------------------------------------------

class TestGetters:

    def test_get_tool_saved(self, saved_registry):
        tool = saved_registry.get_tool("custom_my_tool")
        assert tool is not None
        assert tool.name == "custom_my_tool"

    def test_get_tool_draft(self, registry, sample_tool_kwargs):
        registry.create_draft(**sample_tool_kwargs)
        tool = registry.get_tool("custom_my_tool")
        assert tool is not None

    def test_get_tool_missing(self, registry):
        assert registry.get_tool("custom_nothing") is None

    def test_get_saved_tools(self, saved_registry):
        tools = saved_registry.get_saved_tools()
        assert len(tools) == 1
        assert tools[0].name == "custom_my_tool"

    def test_get_saved_tools_empty(self, registry):
        assert registry.get_saved_tools() == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_corrupt_index_file_handled(self, tmp_path):
        index_path = os.path.join(str(tmp_path), "_index.json")
        with open(index_path, "w") as f:
            f.write("NOT VALID JSON {{{")
        # Should not raise -- handled gracefully
        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        assert reg._saved == {}

    def test_index_is_not_dict_handled(self, tmp_path):
        index_path = os.path.join(str(tmp_path), "_index.json")
        with open(index_path, "w") as f:
            json.dump(["not", "a", "dict"], f)
        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        assert reg._saved == {}

    def test_missing_definition_file_skipped(self, tmp_path):
        # Create index referencing a tool that doesn't exist on disk
        index_path = os.path.join(str(tmp_path), "_index.json")
        with open(index_path, "w") as f:
            json.dump({"custom_ghost": {"description": "g", "group": "g", "version": 1}}, f)
        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        assert "custom_ghost" not in reg._saved

    def test_save_tool_direct(self, registry, sample_tool_kwargs):
        tool = CustomToolDefinition(**sample_tool_kwargs)
        registry.save_tool_direct(tool)
        assert "custom_my_tool" in registry._saved
        # Index updated
        with open(registry._index_file, "r") as f:
            index = json.load(f)
        assert "custom_my_tool" in index


# ---------------------------------------------------------------------------
# TASK-192: Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:

    def test_registry_has_rlock(self, registry):
        """TASK-192: Registry must have a threading.RLock."""
        import threading
        assert hasattr(registry, "_lock")
        assert isinstance(registry._lock, type(threading.RLock()))

    def test_concurrent_create_delete_no_exceptions(self, tmp_path):
        """TASK-192: Concurrent create/delete must not raise."""
        import threading

        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        errors = []

        def create_tools(start_idx, count):
            for i in range(count):
                try:
                    reg.create_draft(
                        name=f"custom_thread_{start_idx}_{i}",
                        description=f"tool {start_idx}_{i}",
                        parameters={},
                        script="result = 1",
                    )
                except Exception as exc:
                    errors.append(exc)

        def delete_tools(start_idx, count):
            for i in range(count):
                try:
                    reg.delete_tool(f"custom_thread_{start_idx}_{i}")
                except Exception as exc:
                    errors.append(exc)

        # Create in parallel
        threads = []
        for batch in range(4):
            t = threading.Thread(target=create_tools, args=(batch, 10))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors during concurrent create: {errors}"

        # Delete in parallel
        threads = []
        for batch in range(4):
            t = threading.Thread(target=delete_tools, args=(batch, 10))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors during concurrent delete: {errors}"


# ---------------------------------------------------------------------------
# TASK-208: Script injection attack vector tests
# ---------------------------------------------------------------------------

class TestScriptInjectionVectors:
    """Document known script injection patterns.

    These tests verify validate_script's current behaviour against known
    injection attack vectors. Tests that *should* fail but currently pass
    are marked with a TODO comment referencing TASK-182/183.
    """

    def test_triple_quote_breakout_in_test_tool(self, tmp_path):
        """TASK-182: Triple quotes in params must NOT break out of the string
        literal in test_tool()'s wrapped script.

        Before the fix, params were interpolated into '''...''' triple-quoted
        strings, allowing breakout via a value containing literal '''.
        After the fix, repr() is used to produce a safe string literal.
        """
        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        reg.create_draft(
            name="custom_injection_test",
            description="injection test",
            parameters={"properties": {"v": {"type": "string"}}},
            script="result = params.get('v', '')",
        )

        malicious_params = {"v": "x'''\nimport os; os.system('whoami')\n'''"}
        captured_script = {}

        def mock_execute(tool_name, args):
            captured_script["script"] = args["script"]
            return {"success": True}

        reg.test_tool("custom_injection_test", malicious_params,
                       execute_fn=mock_execute)

        script = captured_script["script"]
        # The wrapped script must be valid Python syntax
        compile(script, "<test_tool_injection>", "exec")
        # Triple quotes must NOT appear raw in the params line
        assert "'''" not in script, (
            "Triple quotes must not appear unescaped in wrapped script"
        )

    def test_triple_quote_breakout_in_execute_custom_tool(self, tmp_path):
        """TASK-182: Triple quotes in params must NOT break out of the string
        literal in execute_custom_tool()'s wrapped script."""
        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        reg.create_draft(
            name="custom_exec_inject",
            description="exec injection test",
            parameters={"properties": {"v": {"type": "string"}}},
            script="result = params.get('v', '')",
        )
        reg.save_tool("custom_exec_inject")

        malicious_params = {"v": "payload'''\nimport os\n'''"}
        captured_script = {}

        def mock_execute(tool_name, args):
            captured_script["script"] = args["script"]
            return {"success": True}

        reg.execute_custom_tool("custom_exec_inject", malicious_params,
                                execute_fn=mock_execute)

        script = captured_script["script"]
        compile(script, "<exec_tool_injection>", "exec")
        assert "'''" not in script

    def test_backslash_in_params_safe(self, tmp_path):
        """TASK-182: Backslashes in params must be safely escaped."""
        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        reg.create_draft(
            name="custom_backslash_test",
            description="backslash test",
            parameters={"properties": {"path": {"type": "string"}}},
            script="result = params['path']",
        )

        params_with_backslashes = {"path": "C:\\Users\\test\\file.txt"}
        captured = {}

        def mock_execute(tool_name, args):
            captured["script"] = args["script"]
            return {"success": True}

        reg.test_tool("custom_backslash_test", params_with_backslashes,
                       execute_fn=mock_execute)

        script = captured["script"]
        compile(script, "<backslash_test>", "exec")

    def test_newlines_in_params_safe(self, tmp_path):
        """TASK-182: Newline characters in params must not break the script."""
        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        reg.create_draft(
            name="custom_newline_test",
            description="newline test",
            parameters={"properties": {"text": {"type": "string"}}},
            script="result = len(params['text'])",
        )

        params_with_newlines = {"text": "line1\nline2\r\nline3"}
        captured = {}

        def mock_execute(tool_name, args):
            captured["script"] = args["script"]
            return {"success": True}

        reg.test_tool("custom_newline_test", params_with_newlines,
                       execute_fn=mock_execute)

        script = captured["script"]
        compile(script, "<newline_test>", "exec")

    def test_various_quote_styles_safe(self, tmp_path):
        """TASK-182: All quote variants must be safely escaped in params."""
        reg = CustomToolRegistry(tools_dir=str(tmp_path))
        reg.create_draft(
            name="custom_quotes_test",
            description="quotes test",
            parameters={"properties": {"v": {"type": "string"}}},
            script="result = params['v']",
        )
        reg.save_tool("custom_quotes_test")

        # Test various dangerous quote combinations
        attack_vectors = [
            {"v": "'''"},
            {"v": '"""'},
            {"v": "a'''b'''c"},
            {"v": "' ; malicious(); '"},
            {"v": "\"\"\" + __import__('os').system('id') + \"\"\""},
            {"v": "\\'''"},
        ]

        for params in attack_vectors:
            captured = {}

            def mock_execute(tool_name, args):
                captured["script"] = args["script"]
                return {"success": True}

            reg.execute_custom_tool("custom_quotes_test", params,
                                    execute_fn=mock_execute)
            script = captured["script"]
            # Every variant must compile cleanly
            compile(script, "<quote_variant_test>", "exec")

    def test_import_obfuscation_via_getattr(self):
        """getattr(__builtins__, '__import__') bypasses naive 'import' blocklist.

        validate_script has a pattern for getattr(__builtins__ but
        obfuscation variants may slip through.
        """
        # Direct getattr pattern IS detected
        direct = "getattr(__builtins__, '__import__')('os').system('whoami')"
        warnings_direct = validate_script(direct)
        assert len(warnings_direct) > 0, (
            "Direct getattr(__builtins__...) should be caught"
        )

        # Obfuscated variant using string concatenation
        obfuscated = (
            "g = getattr\n"
            "b = __builtins__\n"
            "g(b, '__imp' + 'ort__')('os').system('whoami')"
        )
        warnings_obfuscated = validate_script(obfuscated)
        # TODO: TASK-182/183 -- tighten validation: this obfuscated
        # variant currently evades the regex-based blocklist.
        # When TASK-182/183 is implemented, change to assert len > 0.
        if not warnings_obfuscated:
            pass  # Known gap -- obfuscated getattr not yet detected

    def test_exec_in_string_literal(self):
        """exec() hidden inside a string that gets eval'd.

        A script could smuggle exec inside an eval call.
        """
        # Direct exec IS detected
        direct = "exec('import os')"
        assert len(validate_script(direct)) > 0

        # exec hidden inside string + eval
        hidden = "s = 'ex' + 'ec'\neval(s + \"('import os')\")"
        warnings = validate_script(hidden)
        # eval itself is detected, so at least one warning
        assert len(warnings) > 0, "eval should be detected even if exec is hidden"
        # TODO: TASK-182/183 -- tighten validation: the 'exec' itself
        # is obfuscated via string concatenation and not directly flagged.

    def test_dunder_access(self):
        """__class__.__mro__[1].__subclasses__() sandbox escape pattern.

        Python sandbox escape via the dunder chain:
        ().__class__.__mro__[1].__subclasses__()
        """
        script = "().__class__.__mro__[1].__subclasses__()"
        warnings = validate_script(script)
        # TODO: TASK-182/183 -- tighten validation: dunder chain access
        # is not currently detected by validate_script's regex patterns.
        # When TASK-182/183 is implemented, this should produce warnings.
        if not warnings:
            pass  # Known gap -- dunder chain not yet detected

    def test_compile_obfuscated(self):
        """compile() with obfuscated mode string can bypass detection."""
        # Direct compile IS detected
        direct = "compile('import os', '<string>', 'exec')"
        assert len(validate_script(direct)) > 0

        # Obfuscated via variable
        obfuscated = "c = compile\nc('import os', '<s>', 'exec')"
        warnings = validate_script(obfuscated)
        # TODO: TASK-182/183 -- tighten validation: aliased compile
        # is not detected by the current regex patterns.
        if not warnings:
            pass  # Known gap -- aliased compile not yet detected

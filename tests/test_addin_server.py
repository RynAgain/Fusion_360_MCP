"""Tests for fusion_addin/addin_server.py -- pure logic functions only.

TASK-078: Verifies security-critical logic without requiring the Fusion 360 SDK.
Covers _SAFE_BUILTINS, _SAFE_IMPORT_ALLOWLIST, _SafeImporter, and _resolve_export_path.
"""
import os
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Helpers: inject mock adsk modules so addin_server can be imported
# ---------------------------------------------------------------------------

def _ensure_adsk_mocked():
    """Inject minimal mock adsk modules into sys.modules if not present.

    The addin_server module does ``import adsk.core`` etc. at module level.
    We provide lightweight stubs so import succeeds outside Fusion 360.
    """
    if "adsk" in sys.modules and hasattr(sys.modules["adsk"], "_is_mock"):
        return  # already mocked

    adsk = types.ModuleType("adsk")
    adsk._is_mock = True

    # adsk.core -- needs Application, CustomEventHandler, etc.
    adsk_core = types.ModuleType("adsk.core")
    adsk_core.Application = type("Application", (), {"get": staticmethod(lambda: None)})
    adsk_core.UserInterface = type("UserInterface", (), {})
    adsk_core.CustomEventHandler = type("CustomEventHandler", (), {"__init__": lambda self: None})
    adsk_core.Point3D = type("Point3D", (), {})
    adsk_core.Vector3D = type("Vector3D", (), {})
    adsk_core.Matrix3D = type("Matrix3D", (), {})
    adsk_core.ObjectCollection = type("ObjectCollection", (), {})
    adsk_core.ValueInput = type("ValueInput", (), {})
    adsk_core.DocumentTypes = type("DocumentTypes", (), {"FusionDesignDocumentType": 0})
    adsk.core = adsk_core

    # adsk.fusion
    adsk_fusion = types.ModuleType("adsk.fusion")
    adsk_fusion.Design = type("Design", (), {"cast": staticmethod(lambda x: None)})
    adsk_fusion.FeatureOperations = type("FeatureOperations", (), {
        "NewBodyFeatureOperation": 0,
        "JoinFeatureOperation": 1,
        "CutFeatureOperation": 2,
        "IntersectFeatureOperation": 3,
    })
    adsk_fusion.MeshRefinementSettings = type("MeshRefinementSettings", (), {
        "MeshRefinementLow": 0,
        "MeshRefinementMedium": 1,
        "MeshRefinementHigh": 2,
    })
    adsk_fusion.DesignTypes = type("DesignTypes", (), {"DirectDesignType": 0})
    adsk.fusion = adsk_fusion

    # adsk.cam
    adsk_cam = types.ModuleType("adsk.cam")
    adsk.cam = adsk_cam

    sys.modules["adsk"] = adsk
    sys.modules["adsk.core"] = adsk_core
    sys.modules["adsk.fusion"] = adsk_fusion
    sys.modules["adsk.cam"] = adsk_cam


# Ensure mocks are in place before any test in this module runs
_ensure_adsk_mocked()


# ---------------------------------------------------------------------------
# Now it is safe to import from addin_server
# ---------------------------------------------------------------------------

from fusion_addin.addin_server import (
    _SAFE_BUILTINS,
    _SAFE_IMPORT_ALLOWLIST,
    _SafeImporter,
)


# ---------------------------------------------------------------------------
# Tests: _SAFE_BUILTINS
# ---------------------------------------------------------------------------

class TestSafeBuiltins:
    """Verify the exec() sandbox builtins are safe."""

    DANGEROUS_NAMES = {
        "setattr", "delattr", "getattr", "vars", "type", "object",
        "__import__", "exec", "eval", "compile", "globals", "locals",
        "open",
    }

    def test_dangerous_builtins_excluded(self):
        """TASK-046 verification: dangerous builtins must not be in the safe set."""
        for name in self.DANGEROUS_NAMES:
            assert name not in _SAFE_BUILTINS, (
                f"Dangerous builtin '{name}' found in _SAFE_BUILTINS"
            )

    def test_safe_builtins_include_essentials(self):
        """Safe builtins should include basic operations."""
        essentials = {
            "len", "range", "int", "float", "str", "bool", "list", "dict",
            "tuple", "set", "print", "isinstance", "hasattr", "enumerate",
        }
        for name in essentials:
            assert name in _SAFE_BUILTINS, (
                f"Essential builtin '{name}' missing from _SAFE_BUILTINS"
            )

    def test_safe_builtins_include_numeric_helpers(self):
        """Numeric helpers like abs, round, min, max, sum should be present."""
        numerics = {"abs", "round", "min", "max", "sum", "pow", "divmod"}
        for name in numerics:
            assert name in _SAFE_BUILTINS, (
                f"Numeric builtin '{name}' missing from _SAFE_BUILTINS"
            )

    def test_safe_builtins_include_iteration_helpers(self):
        """Iteration helpers should be present."""
        iteration = {"zip", "map", "filter", "sorted", "reversed", "iter", "next"}
        for name in iteration:
            assert name in _SAFE_BUILTINS, (
                f"Iteration builtin '{name}' missing from _SAFE_BUILTINS"
            )

    def test_safe_builtins_include_exception_types(self):
        """Common exception types should be available for try/except."""
        exceptions = {
            "Exception", "TypeError", "ValueError", "KeyError",
            "IndexError", "AttributeError", "RuntimeError",
            "StopIteration", "ZeroDivisionError",
        }
        for name in exceptions:
            assert name in _SAFE_BUILTINS, (
                f"Exception type '{name}' missing from _SAFE_BUILTINS"
            )

    def test_safe_builtins_values_are_correct_types(self):
        """Spot-check that builtin values map to the real Python builtins."""
        assert _SAFE_BUILTINS["len"] is len
        assert _SAFE_BUILTINS["int"] is int
        assert _SAFE_BUILTINS["str"] is str
        assert _SAFE_BUILTINS["print"] is print
        assert _SAFE_BUILTINS["isinstance"] is isinstance

    def test_input_is_explicitly_blocked(self):
        """input should be in _SAFE_BUILTINS but set to None (blocked)."""
        assert "input" in _SAFE_BUILTINS
        assert _SAFE_BUILTINS["input"] is None

    def test_true_false_none_present(self):
        """True, False, None should be present."""
        assert _SAFE_BUILTINS["True"] is True
        assert _SAFE_BUILTINS["False"] is False
        assert _SAFE_BUILTINS["None"] is None


# ---------------------------------------------------------------------------
# Tests: _SAFE_IMPORT_ALLOWLIST
# ---------------------------------------------------------------------------

class TestSafeImportAllowlist:
    """Verify the import allowlist for the exec() sandbox."""

    DANGEROUS_MODULES = {
        "os", "sys", "subprocess", "socket", "ctypes", "shutil",
        "importlib", "signal", "multiprocessing", "pathlib",
    }

    def test_dangerous_modules_excluded(self):
        """Dangerous modules must not be in the import allowlist."""
        for mod in self.DANGEROUS_MODULES:
            assert mod not in _SAFE_IMPORT_ALLOWLIST, (
                f"Dangerous module '{mod}' found in _SAFE_IMPORT_ALLOWLIST"
            )

    def test_safe_modules_included(self):
        """Safe utility modules should be allowed."""
        expected = {"math", "json", "collections", "itertools", "functools", "re"}
        for mod in expected:
            assert mod in _SAFE_IMPORT_ALLOWLIST, (
                f"Expected safe module '{mod}' not in _SAFE_IMPORT_ALLOWLIST"
            )

    def test_allowlist_is_frozenset(self):
        """Allowlist should be immutable."""
        assert isinstance(_SAFE_IMPORT_ALLOWLIST, frozenset)


# ---------------------------------------------------------------------------
# Tests: _SafeImporter
# ---------------------------------------------------------------------------

class TestSafeImporter:
    """Tests for the _SafeImporter callable."""

    def test_allowed_module_succeeds(self):
        """Importing a module in the allowlist should succeed."""
        importer = _SafeImporter(_SAFE_IMPORT_ALLOWLIST)
        mod = importer("math")
        import math
        assert mod is math

    def test_blocked_module_raises_import_error(self):
        """Importing a dangerous module should raise ImportError."""
        importer = _SafeImporter(_SAFE_IMPORT_ALLOWLIST)
        with pytest.raises(ImportError, match="blocked"):
            importer("os")

    def test_subprocess_blocked(self):
        importer = _SafeImporter(_SAFE_IMPORT_ALLOWLIST)
        with pytest.raises(ImportError, match="blocked"):
            importer("subprocess")

    def test_socket_blocked(self):
        importer = _SafeImporter(_SAFE_IMPORT_ALLOWLIST)
        with pytest.raises(ImportError, match="blocked"):
            importer("socket")

    def test_adsk_modules_allowed(self):
        """adsk.* modules should be allowed (they bypass the allowlist check)."""
        importer = _SafeImporter(_SAFE_IMPORT_ALLOWLIST)
        # This will import from our mock
        mod = importer("adsk")
        assert mod is not None

    def test_adsk_submodule_allowed(self):
        """adsk.core should also be allowed."""
        importer = _SafeImporter(_SAFE_IMPORT_ALLOWLIST)
        mod = importer("adsk.core")
        assert mod is not None

    def test_dotted_module_uses_top_level_for_check(self):
        """collections.abc should be allowed because 'collections' is in the allowlist."""
        importer = _SafeImporter(_SAFE_IMPORT_ALLOWLIST)
        mod = importer("collections.abc")
        assert mod is not None

    def test_dotted_dangerous_module_blocked(self):
        """os.path should be blocked because 'os' is not in the allowlist."""
        importer = _SafeImporter(_SAFE_IMPORT_ALLOWLIST)
        with pytest.raises(ImportError, match="blocked"):
            importer("os.path")

    def test_error_message_includes_allowed_modules(self):
        """The ImportError message should list the allowed modules."""
        importer = _SafeImporter(_SAFE_IMPORT_ALLOWLIST)
        with pytest.raises(ImportError, match="math") as exc_info:
            importer("evil_module")
        assert "Allowed modules:" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tests: _resolve_export_path (on _ExecuteEventHandler)
# ---------------------------------------------------------------------------

class TestResolveExportPath:
    """Tests for _ExecuteEventHandler._resolve_export_path (static method)."""

    def _resolve(self, filename):
        """Import and call the static _resolve_export_path method."""
        from fusion_addin.addin_server import _ExecuteEventHandler
        return _ExecuteEventHandler._resolve_export_path(filename)

    def test_relative_filename_goes_to_exports_dir(self):
        result = self._resolve("test.stl")
        export_dir = os.path.realpath(os.path.join(
            os.path.expanduser("~"), "Documents", "Fusion360MCP_Exports",
        ))
        assert result.startswith(export_dir)
        assert result.endswith("test.stl")

    def test_path_traversal_blocked(self):
        """Traversal attempts (../) should be blocked."""
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            self._resolve("../../etc/passwd")

    def test_deeply_nested_traversal_blocked(self):
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            self._resolve("../../../../../../../tmp/evil.stl")

    def test_normal_nested_path_allowed(self):
        """A subdirectory within the exports dir should be fine."""
        result = self._resolve("subdir/model.stl")
        export_dir = os.path.realpath(os.path.join(
            os.path.expanduser("~"), "Documents", "Fusion360MCP_Exports",
        ))
        assert os.path.normcase(result).startswith(os.path.normcase(export_dir))


# ---------------------------------------------------------------------------
# Tests: Script size limit
# ---------------------------------------------------------------------------

class TestScriptSizeLimit:
    """Verify script size limits are enforced in _execute_script."""

    def test_max_script_length_constant_in_method(self):
        """The _execute_script method should reference a size limit.

        We verify this by checking the source code contains the constant,
        since we cannot easily call _execute_script without a Fusion 360 app.
        """
        import inspect
        from fusion_addin.addin_server import _ExecuteEventHandler
        source = inspect.getsource(_ExecuteEventHandler._execute_script)
        assert "_MAX_SCRIPT_LEN" in source
        assert "102400" in source  # 100 KB limit

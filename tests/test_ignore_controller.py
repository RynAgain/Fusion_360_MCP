"""Tests for ai/ignore_controller.py (TASK-163, TASK-186, TASK-209, TASK-210)."""

import os
import tempfile
import textwrap

import pytest

from ai.ignore_controller import (
    IgnoreController,
    get_ignore_controller,
    reset_ignore_controller,
)


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory with no .artifexignore."""
    return str(tmp_path)


@pytest.fixture
def controller(tmp_project):
    """IgnoreController with no custom patterns (no .artifexignore file)."""
    return IgnoreController(project_root=tmp_project)


# ---------------------------------------------------------------------------
# Built-in pattern tests
# ---------------------------------------------------------------------------

class TestBuiltinPatterns:
    """Built-in patterns should block sensitive files regardless of config."""

    def test_blocks_env_file(self, controller):
        assert controller.is_blocked(".env") is True

    def test_blocks_env_local(self, controller):
        assert controller.is_blocked(".env.local") is True

    def test_blocks_env_production(self, controller):
        assert controller.is_blocked(".env.production") is True

    def test_blocks_key_file(self, controller):
        assert controller.is_blocked("secret.key") is True

    def test_blocks_pem_file(self, controller):
        assert controller.is_blocked("cert.pem") is True

    def test_blocks_p12_file(self, controller):
        assert controller.is_blocked("keystore.p12") is True

    def test_blocks_pfx_file(self, controller):
        assert controller.is_blocked("certificate.pfx") is True

    def test_blocks_nested_key(self, controller):
        assert controller.is_blocked("certs/server.key") is True

    def test_blocks_git_config(self, controller):
        assert controller.is_blocked(".git/config") is True

    def test_blocks_git_objects(self, controller):
        assert controller.is_blocked(".git/objects/ab/cdef1234") is True

    def test_blocks_pycache(self, controller):
        assert controller.is_blocked("__pycache__/module.cpython-312.pyc") is True

    def test_blocks_nested_pycache(self, controller):
        assert controller.is_blocked("ai/__pycache__/modes.cpython-312.pyc") is True

    def test_blocks_pyc_file(self, controller):
        assert controller.is_blocked("module.pyc") is True

    def test_blocks_secrets_directory(self, controller):
        assert controller.is_blocked("secrets/api_token.txt") is True

    def test_blocks_nested_secrets(self, controller):
        assert controller.is_blocked("config/secrets/db_password.txt") is True

    def test_blocks_credentials_directory(self, controller):
        assert controller.is_blocked("credentials/oauth.json") is True


# ---------------------------------------------------------------------------
# Normal (non-blocked) files
# ---------------------------------------------------------------------------

class TestAllowedFiles:
    """Normal project files should NOT be blocked."""

    def test_allows_main_py(self, controller):
        assert controller.is_blocked("main.py") is False

    def test_allows_readme(self, controller):
        assert controller.is_blocked("README.md") is False

    def test_allows_source_file(self, controller):
        assert controller.is_blocked("ai/modes.py") is False

    def test_allows_test_file(self, controller):
        assert controller.is_blocked("tests/test_ignore_controller.py") is False

    def test_allows_config_settings(self, controller):
        assert controller.is_blocked("config/settings.py") is False

    def test_allows_html_template(self, controller):
        assert controller.is_blocked("web/templates/index.html") is False

    def test_allows_css_file(self, controller):
        assert controller.is_blocked("web/static/css/style.css") is False

    def test_allows_json_data(self, controller):
        assert controller.is_blocked("data/design_states/snapshot.json") is False


# ---------------------------------------------------------------------------
# Custom patterns from .artifexignore
# ---------------------------------------------------------------------------

class TestCustomPatterns:
    """Patterns loaded from .artifexignore should be applied."""

    def test_loads_custom_patterns(self, tmp_path):
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text(textwrap.dedent("""\
            # Comment line
            data/conversations/*
            data/.secret_key

            # Build artifacts
            *.egg-info/
            dist/
            build/
        """))
        ctrl = IgnoreController(project_root=str(tmp_path))
        # 3 non-empty, non-comment lines from custom + all built-ins
        assert len(ctrl._custom_patterns) == 5

    def test_custom_pattern_blocks_matching_file(self, tmp_path):
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("docs/internal/*\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert ctrl.is_blocked(os.path.join(str(tmp_path), "docs", "internal", "notes.md")) is True

    def test_custom_pattern_allows_non_matching(self, tmp_path):
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("docs/internal/*\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert ctrl.is_blocked(os.path.join(str(tmp_path), "docs", "public", "README.md")) is False

    def test_skips_comments_and_blanks(self, tmp_path):
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("# comment\n\n  \npattern_one\n# another comment\npattern_two\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert ctrl._custom_patterns == ["pattern_one", "pattern_two"]

    def test_all_patterns_combines_builtin_and_custom(self, tmp_path):
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("custom_pattern\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        all_p = ctrl.all_patterns
        assert "custom_pattern" in all_p
        assert ".env" in all_p  # built-in
        assert len(all_p) == len(IgnoreController.BUILTIN_PATTERNS) + 1


# ---------------------------------------------------------------------------
# TASK-186: Gitignore-compatible matching (pathspec)
# ---------------------------------------------------------------------------

class TestGitignoreSemantics:
    """TASK-186: Verify gitignore-style pattern matching via pathspec."""

    def test_recursive_glob_matches_nested_file(self, tmp_path):
        """*.log should match foo/bar/debug.log (recursive by default in gitignore)."""
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("*.log\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert ctrl.is_blocked("debug.log") is True
        assert ctrl.is_blocked("foo/bar/debug.log") is True

    def test_doublestar_pattern_matches_deep_nesting(self, tmp_path):
        """**/build/** should match any build directory at any depth."""
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("**/build/**\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert ctrl.is_blocked("build/output.bin") is True
        assert ctrl.is_blocked("project/build/output.bin") is True
        assert ctrl.is_blocked("a/b/build/c/d.txt") is True

    def test_doublestar_in_middle(self, tmp_path):
        """foo/**/bar should match foo/bar, foo/a/bar, foo/a/b/bar."""
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("foo/**/bar\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert ctrl.is_blocked("foo/bar") is True
        assert ctrl.is_blocked("foo/a/bar") is True
        assert ctrl.is_blocked("foo/a/b/bar") is True

    def test_rooted_pattern_with_leading_slash(self, tmp_path):
        """/root_only.txt should only match at the root, not in subdirectories."""
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("/root_only.txt\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert ctrl.is_blocked("root_only.txt") is True
        assert ctrl.is_blocked("subdir/root_only.txt") is False

    def test_pattern_with_directory_separator_matches_full_path(self, tmp_path):
        """Patterns containing / match against the full relative path."""
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("logs/app.log\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert ctrl.is_blocked("logs/app.log") is True
        assert ctrl.is_blocked("other/logs/app.log") is False

    def test_trailing_slash_pattern(self, tmp_path):
        """Trailing slash (directory marker) should match paths under that dir."""
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("dist/\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert ctrl.is_blocked("dist/bundle.js") is True

    def test_builtin_key_pattern_recursive(self, controller):
        """Built-in *.key pattern should match deeply nested key files."""
        assert controller.is_blocked("a/b/c/private.key") is True

    def test_builtin_env_pattern_recursive(self, controller):
        """Built-in .env.* pattern should match nested .env files."""
        assert controller.is_blocked("config/.env.production") is True

    def test_negation_pattern(self, tmp_path):
        """! prefix should negate a previously matching pattern."""
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("*.log\n!important.log\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert ctrl.is_blocked("debug.log") is True
        assert ctrl.is_blocked("important.log") is False


# ---------------------------------------------------------------------------
# filter_paths
# ---------------------------------------------------------------------------

class TestFilterPaths:
    """filter_paths should return only non-blocked paths."""

    def test_filters_blocked_paths(self, controller):
        paths = ["main.py", ".env", "README.md", "secret.key", "ai/modes.py"]
        allowed = controller.filter_paths(paths)
        assert "main.py" in allowed
        assert "README.md" in allowed
        assert "ai/modes.py" in allowed
        assert ".env" not in allowed
        assert "secret.key" not in allowed

    def test_empty_list_returns_empty(self, controller):
        assert controller.filter_paths([]) == []

    def test_all_blocked_returns_empty(self, controller):
        paths = [".env", ".env.local", "server.key"]
        assert controller.filter_paths(paths) == []

    def test_none_blocked_returns_all(self, controller):
        paths = ["main.py", "README.md", "ai/modes.py"]
        assert controller.filter_paths(paths) == paths


# ---------------------------------------------------------------------------
# reload
# ---------------------------------------------------------------------------

class TestReload:
    """reload() should pick up changes to .artifexignore."""

    def test_reload_picks_up_new_patterns(self, tmp_path):
        # Start with no ignore file
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert ctrl._custom_patterns == []

        # Create ignore file
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("new_pattern\n")
        ctrl.reload()
        assert "new_pattern" in ctrl._custom_patterns

    def test_reload_clears_old_patterns(self, tmp_path):
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("old_pattern\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert "old_pattern" in ctrl._custom_patterns

        # Rewrite the file
        ignore_file.write_text("new_pattern\n")
        ctrl.reload()
        assert "old_pattern" not in ctrl._custom_patterns
        assert "new_pattern" in ctrl._custom_patterns

    def test_reload_handles_deleted_file(self, tmp_path):
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("some_pattern\n")
        ctrl = IgnoreController(project_root=str(tmp_path))
        assert len(ctrl._custom_patterns) == 1

        ignore_file.unlink()
        ctrl.reload()
        assert ctrl._custom_patterns == []


# ---------------------------------------------------------------------------
# Windows backslash paths
# ---------------------------------------------------------------------------

class TestBackslashPaths:
    """Paths with Windows backslashes should be handled correctly."""

    def test_backslash_path_blocked(self, controller):
        # Simulating a Windows-style path relative to project root
        assert controller.is_blocked("secrets\\api_token.txt") is True

    def test_backslash_env_path(self, controller):
        assert controller.is_blocked("config\\.env") is True

    def test_backslash_key_path(self, controller):
        assert controller.is_blocked("certs\\server.key") is True

    def test_backslash_normal_file_allowed(self, controller):
        assert controller.is_blocked("ai\\modes.py") is False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    """get_ignore_controller() returns a singleton instance."""

    def test_returns_instance(self):
        reset_ignore_controller()
        ctrl = get_ignore_controller()
        assert isinstance(ctrl, IgnoreController)

    def test_returns_same_instance(self):
        reset_ignore_controller()
        ctrl1 = get_ignore_controller()
        ctrl2 = get_ignore_controller()
        assert ctrl1 is ctrl2

    def test_reset_clears_singleton(self):
        """TASK-210: reset_ignore_controller() clears the singleton state."""
        reset_ignore_controller()
        ctrl1 = get_ignore_controller()
        reset_ignore_controller()
        ctrl2 = get_ignore_controller()
        assert ctrl1 is not ctrl2


# ---------------------------------------------------------------------------
# TASK-209: Windows drive letter edge cases
# ---------------------------------------------------------------------------

class TestWindowsPathEdgeCases:
    """Windows-specific path handling: drive letters, mixed separators, UNC."""

    def test_drive_letter_path_matching(self, tmp_path):
        """Paths with Windows drive letters should still match patterns."""
        # Use a project root with a drive-letter-like prefix
        project_root = str(tmp_path)
        ctrl = IgnoreController(project_root=project_root)
        # Construct an absolute path under project root
        abs_env = os.path.join(project_root, "secret.env")
        # .env.* won't match but *.key would -- use a key file instead
        abs_key = os.path.join(project_root, "private.key")
        assert ctrl.is_blocked(abs_key) is True

    def test_drive_letter_env_file(self, tmp_path):
        """Drive-letter absolute path to .env should be blocked."""
        project_root = str(tmp_path)
        ctrl = IgnoreController(project_root=project_root)
        abs_env = os.path.join(project_root, ".env")
        assert ctrl.is_blocked(abs_env) is True

    def test_mixed_separator_path(self, tmp_path):
        """Mixed forward/back slashes should normalize correctly."""
        project_root = str(tmp_path)
        ignore_file = tmp_path / ".artifexignore"
        ignore_file.write_text("logs/*.log\n")
        ctrl = IgnoreController(project_root=project_root)
        # Build a path with mixed separators
        mixed = project_root + "/logs\\debug.log"
        assert ctrl.is_blocked(mixed) is True

    def test_backslash_only_absolute_path(self, tmp_path):
        """Fully backslash-separated absolute path should match."""
        project_root = str(tmp_path)
        ctrl = IgnoreController(project_root=project_root)
        abs_path = project_root + "\\secrets\\api_token.txt"
        assert ctrl.is_blocked(abs_path) is True

    def test_mixed_separators_allowed_file(self, tmp_path):
        """Mixed separators on an allowed file should not false-positive."""
        project_root = str(tmp_path)
        ctrl = IgnoreController(project_root=project_root)
        mixed = project_root + "/ai\\modes.py"
        assert ctrl.is_blocked(mixed) is False

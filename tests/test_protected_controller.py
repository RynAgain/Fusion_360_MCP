"""Tests for ai/protected_controller.py (TASK-164, TASK-210, TASK-211)."""

import os

import pytest

from ai.protected_controller import (
    ProtectedController,
    get_protected_controller,
    reset_protected_controller,
)


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory."""
    return str(tmp_path)


@pytest.fixture
def controller(tmp_project):
    """ProtectedController rooted at a temp directory."""
    return ProtectedController(project_root=tmp_project)


# ---------------------------------------------------------------------------
# Config files are protected
# ---------------------------------------------------------------------------

class TestConfigProtection:
    """Files under config/ should be protected."""

    def test_config_settings_py(self, controller):
        assert controller.is_protected("config/settings.py") is True

    def test_config_config_json(self, controller):
        assert controller.is_protected("config/config.json") is True

    def test_config_init_py(self, controller):
        assert controller.is_protected("config/__init__.py") is True

    def test_nested_config_file(self, controller):
        assert controller.is_protected("config/rules/example.md") is True


# ---------------------------------------------------------------------------
# Env files are protected
# ---------------------------------------------------------------------------

class TestEnvProtection:
    """Environment files should be protected."""

    def test_env_file(self, controller):
        assert controller.is_protected(".env") is True

    def test_env_local(self, controller):
        assert controller.is_protected(".env.local") is True

    def test_env_production(self, controller):
        assert controller.is_protected(".env.production") is True


# ---------------------------------------------------------------------------
# Security files are protected
# ---------------------------------------------------------------------------

class TestSecurityFileProtection:
    """Private keys and certificates should be protected."""

    def test_key_file(self, controller):
        assert controller.is_protected("server.key") is True

    def test_pem_file(self, controller):
        assert controller.is_protected("cert.pem") is True

    def test_p12_file(self, controller):
        assert controller.is_protected("keystore.p12") is True

    def test_pfx_file(self, controller):
        assert controller.is_protected("certificate.pfx") is True

    def test_nested_key_file(self, controller):
        assert controller.is_protected("certs/server.key") is True

    def test_manifest_file(self, controller):
        assert controller.is_protected("fusion_addin/Fusion360MCP.manifest") is True


# ---------------------------------------------------------------------------
# Project files are protected
# ---------------------------------------------------------------------------

class TestProjectFileProtection:
    """Critical project files should be protected."""

    def test_requirements_txt(self, controller):
        assert controller.is_protected("requirements.txt") is True

    def test_main_py(self, controller):
        assert controller.is_protected("main.py") is True

    def test_setup_py(self, controller):
        assert controller.is_protected("setup.py") is True

    def test_setup_cfg(self, controller):
        assert controller.is_protected("setup.cfg") is True

    def test_pyproject_toml(self, controller):
        assert controller.is_protected("pyproject.toml") is True

    def test_gitignore(self, controller):
        assert controller.is_protected(".gitignore") is True

    def test_gitattributes(self, controller):
        assert controller.is_protected(".gitattributes") is True

    def test_artifexignore(self, controller):
        assert controller.is_protected(".artifexignore") is True

    def test_artifexmodes(self, controller):
        assert controller.is_protected(".artifexmodes") is True


# ---------------------------------------------------------------------------
# Normal source files are NOT protected
# ---------------------------------------------------------------------------

class TestNonProtectedFiles:
    """Regular source and data files should NOT be protected."""

    def test_ai_modes(self, controller):
        assert controller.is_protected("ai/modes.py") is False

    def test_web_routes(self, controller):
        assert controller.is_protected("web/routes.py") is False

    def test_fusion_bridge(self, controller):
        assert controller.is_protected("fusion/bridge.py") is False

    def test_test_file(self, controller):
        assert controller.is_protected("tests/test_modes.py") is False

    def test_ai_init(self, controller):
        assert controller.is_protected("ai/__init__.py") is False

    def test_mcp_server(self, controller):
        assert controller.is_protected("mcp/server.py") is False

    def test_html_template(self, controller):
        assert controller.is_protected("web/templates/index.html") is False

    def test_css_file(self, controller):
        assert controller.is_protected("web/static/css/style.css") is False

    def test_javascript_file(self, controller):
        assert controller.is_protected("web/static/js/app.js") is False

    def test_readme(self, controller):
        assert controller.is_protected("README.md") is False


# ---------------------------------------------------------------------------
# Data files are NOT protected
# ---------------------------------------------------------------------------

class TestDataFilesNotProtected:
    """Data files should NOT be protected."""

    def test_conversation_json(self, controller):
        assert controller.is_protected("data/conversations/xyz.json") is False

    def test_design_state(self, controller):
        assert controller.is_protected("data/design_states/snapshot.json") is False

    def test_uploads(self, controller):
        assert controller.is_protected("data/uploads/model.stl") is False

    def test_ollama_cache(self, controller):
        assert controller.is_protected("data/ollama_models_cache.json") is False


# ---------------------------------------------------------------------------
# Windows backslash paths
# ---------------------------------------------------------------------------

class TestBackslashPaths:
    """Paths with Windows backslashes should work correctly."""

    def test_backslash_config_path(self, controller):
        assert controller.is_protected("config\\settings.py") is True

    def test_backslash_normal_path(self, controller):
        assert controller.is_protected("ai\\modes.py") is False

    def test_backslash_key_path(self, controller):
        assert controller.is_protected("certs\\server.key") is True


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    """get_protected_controller() returns a singleton instance."""

    def test_returns_instance(self):
        reset_protected_controller()
        ctrl = get_protected_controller()
        assert isinstance(ctrl, ProtectedController)

    def test_returns_same_instance(self):
        reset_protected_controller()
        ctrl1 = get_protected_controller()
        ctrl2 = get_protected_controller()
        assert ctrl1 is ctrl2

    def test_reset_clears_singleton(self):
        """TASK-210: reset_protected_controller() clears the singleton state."""
        reset_protected_controller()
        ctrl1 = get_protected_controller()
        reset_protected_controller()
        ctrl2 = get_protected_controller()
        assert ctrl1 is not ctrl2


# ---------------------------------------------------------------------------
# TASK-211: Gitignore-compatible matching (pathspec) for protected controller
# ---------------------------------------------------------------------------

class TestGitignoreSemantics:
    """TASK-211: Verify gitignore-style pattern matching via pathspec,
    mirroring the approach used by IgnoreController (TASK-186)."""

    def test_doublestar_config_matches_nested(self, controller):
        """config/** should match deeply nested files under config/."""
        assert controller.is_protected("config/rules/example.md") is True
        assert controller.is_protected("config/locales/en.json") is True

    def test_doublestar_config_matches_deep_nesting(self, controller):
        """config/** should match files at arbitrary depth."""
        assert controller.is_protected("config/a/b/c/deep.yml") is True

    def test_recursive_key_pattern(self, controller):
        """*.key should match key files at any depth (gitignore default)."""
        assert controller.is_protected("a/b/c/private.key") is True

    def test_recursive_manifest_pattern(self, controller):
        """*.manifest should match manifest files at any depth."""
        assert controller.is_protected("some/deep/path/app.manifest") is True

    def test_env_dot_star_matches_variants(self, controller):
        """TASK-211: .env.* should match .env.local, .env.production, etc."""
        assert controller.is_protected(".env.local") is True
        assert controller.is_protected(".env.production") is True

    def test_non_config_directory_not_protected(self, controller):
        """Files outside config/ should not match the config/** pattern."""
        assert controller.is_protected("notconfig/settings.py") is False

    def test_backslash_deep_config(self, controller):
        """Backslash-separated deep config paths should still match."""
        assert controller.is_protected("config\\rules\\fusion.md") is True

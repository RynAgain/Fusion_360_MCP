"""Tests for ai/rules_loader.py -- hierarchical rule loading system."""
import os
import pytest

from ai.rules_loader import load_rules, _load_dir, list_rule_files


@pytest.fixture
def rules_env(tmp_path, monkeypatch):
    """
    Set up a temporary directory structure that mimics the rules hierarchy,
    and monkeypatch the module constants so load_rules reads from tmp_path.
    """
    import ai.rules_loader as mod

    global_dir = tmp_path / "config" / "rules"
    global_dir.mkdir(parents=True)

    project_dir = tmp_path / ".f360-rules"
    # Don't create project_dir by default -- tests create it when needed

    monkeypatch.setattr(mod, "RULES_DIRS", [
        str(global_dir),
        str(project_dir),
    ])
    monkeypatch.setattr(mod, "MODE_RULES_PATTERN", str(tmp_path / "config" / "rules-{}"))
    monkeypatch.setattr(mod, "PROJECT_ROOT", str(tmp_path))

    return {
        "root": tmp_path,
        "global_dir": global_dir,
        "project_dir": project_dir,
    }


class TestLoadRules:
    """Tests for load_rules() and related functions."""

    def test_load_empty_rules(self, rules_env):
        """No rule files in existing directories returns empty string."""
        result = load_rules()
        assert result == ""

    def test_load_global_rules(self, rules_env):
        """Loads .md files from config/rules/."""
        gd = rules_env["global_dir"]
        (gd / "01-units.md").write_text("All dimensions in inches.", encoding="utf-8")
        (gd / "02-naming.md").write_text("Prefix bodies with PRJ-.", encoding="utf-8")

        result = load_rules()
        assert "All dimensions in inches." in result
        assert "Prefix bodies with PRJ-." in result
        assert "### 01-units.md" in result
        assert "### 02-naming.md" in result

    def test_load_txt_files(self, rules_env):
        """Loads .txt files in addition to .md."""
        gd = rules_env["global_dir"]
        (gd / "rule.txt").write_text("Text file rule.", encoding="utf-8")

        result = load_rules()
        assert "Text file rule." in result

    def test_load_mode_rules(self, rules_env):
        """Loads from config/rules-{mode}/ when mode is specified."""
        mode_dir = rules_env["root"] / "config" / "rules-sketch"
        mode_dir.mkdir(parents=True)
        (mode_dir / "sketch-rules.md").write_text("Close all profiles.", encoding="utf-8")

        result = load_rules(mode="sketch")
        assert "Close all profiles." in result
        assert "Rules for sketch mode" in result

    def test_load_rules_with_mode(self, rules_env):
        """Combines global and mode-specific rules."""
        gd = rules_env["global_dir"]
        (gd / "global.md").write_text("Global rule.", encoding="utf-8")

        mode_dir = rules_env["root"] / "config" / "rules-feature"
        mode_dir.mkdir(parents=True)
        (mode_dir / "feature.md").write_text("Feature mode rule.", encoding="utf-8")

        result = load_rules(mode="feature")
        assert "Global rule." in result
        assert "Feature mode rule." in result

    def test_full_mode_skips_mode_rules(self, rules_env):
        """Mode 'full' should not load mode-specific rules."""
        mode_dir = rules_env["root"] / "config" / "rules-full"
        mode_dir.mkdir(parents=True)
        (mode_dir / "rule.md").write_text("Should not appear.", encoding="utf-8")

        gd = rules_env["global_dir"]
        (gd / "global.md").write_text("Global only.", encoding="utf-8")

        result = load_rules(mode="full")
        assert "Global only." in result
        assert "Should not appear." not in result

    def test_project_rules(self, rules_env):
        """Loads rules from .f360-rules/ project directory."""
        pd = rules_env["project_dir"]
        pd.mkdir(parents=True)
        (pd / "project.md").write_text("Project-specific rule.", encoding="utf-8")

        result = load_rules()
        assert "Project-specific rule." in result

    def test_nonexistent_directory(self, rules_env):
        """Handles missing directories gracefully."""
        # _load_dir with nonexistent path should return ""
        result = _load_dir("/nonexistent/path/that/does/not/exist")
        assert result == ""

    def test_files_sorted_alphabetically(self, rules_env):
        """Rule files are loaded in sorted order."""
        gd = rules_env["global_dir"]
        (gd / "02-second.md").write_text("Second.", encoding="utf-8")
        (gd / "01-first.md").write_text("First.", encoding="utf-8")
        (gd / "03-third.md").write_text("Third.", encoding="utf-8")

        result = load_rules()
        first_pos = result.index("First.")
        second_pos = result.index("Second.")
        third_pos = result.index("Third.")
        assert first_pos < second_pos < third_pos

    def test_empty_files_skipped(self, rules_env):
        """Empty rule files are not included."""
        gd = rules_env["global_dir"]
        (gd / "empty.md").write_text("", encoding="utf-8")
        (gd / "real.md").write_text("Real content.", encoding="utf-8")

        result = load_rules()
        assert "empty.md" not in result
        assert "Real content." in result

    def test_non_rule_extensions_ignored(self, rules_env):
        """Files with extensions other than .md/.txt are ignored."""
        gd = rules_env["global_dir"]
        (gd / "notes.py").write_text("# python file", encoding="utf-8")
        (gd / "data.json").write_text('{"key": "value"}', encoding="utf-8")
        (gd / "rule.md").write_text("Valid rule.", encoding="utf-8")

        result = load_rules()
        assert "python file" not in result
        assert "key" not in result
        assert "Valid rule." in result


class TestListRuleFiles:
    """Tests for list_rule_files()."""

    def test_list_rule_files(self, rules_env):
        gd = rules_env["global_dir"]
        (gd / "rule1.md").write_text("Rule 1.", encoding="utf-8")
        (gd / "rule2.txt").write_text("Rule 2.", encoding="utf-8")

        mode_dir = rules_env["root"] / "config" / "rules-sketch"
        mode_dir.mkdir(parents=True)
        (mode_dir / "sketch.md").write_text("Sketch rule.", encoding="utf-8")

        files = list_rule_files()
        assert len(files) == 3

        sources = {f['source'] for f in files}
        assert 'global' in sources
        assert 'mode:sketch' in sources

        names = {f['name'] for f in files}
        assert 'rule1.md' in names
        assert 'rule2.txt' in names
        assert 'sketch.md' in names

    def test_list_empty(self, rules_env):
        """No files returns empty list."""
        files = list_rule_files()
        assert files == []

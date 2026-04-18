"""Tests for ai/rules_loader.py -- hierarchical rule loading system."""
import os
import pytest

from ai.rules_loader import (
    load_rules, _load_dir, list_rule_files,
    load_skill, list_skills,
    _parse_yaml_frontmatter, _extract_section, _extract_list_items,
)


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


# ===================================================================
# Structured Markdown-as-Skill protocol tests
# ===================================================================

SAMPLE_SKILL = """\
---
name: Test Skill
version: 2.0
mode: design
autonomous: true
---

# Test Skill Protocol

## Setup

1. Step one
2. Step two
3. Step three

## Constraints

- Rule alpha
- Rule beta
- Rule gamma

## Execution

1. Do thing A
2. Do thing B
3. Check result

## Output Format

- `status`: success | failure
- `result`: description
"""

SKILL_NO_SECTIONS = """\
---
name: Bare Skill
version: 1.0
mode: basic
autonomous: false
---

# Bare Skill

Just some text with no structured sections.
"""


class TestParseYamlFrontmatter:
    """Tests for YAML frontmatter parsing."""

    def test_parse_simple_frontmatter(self):
        metadata, body = _parse_yaml_frontmatter(SAMPLE_SKILL)
        assert metadata["name"] == "Test Skill"
        assert metadata["version"] == 2.0
        assert metadata["mode"] == "design"
        assert metadata["autonomous"] is True

    def test_no_frontmatter(self):
        metadata, body = _parse_yaml_frontmatter("# Just a heading\n\nSome text.")
        assert metadata == {}
        assert "Just a heading" in body

    def test_boolean_true_variants(self):
        text = "---\nflag1: true\nflag2: yes\nflag3: True\nflag4: Yes\n---\nBody"
        metadata, _ = _parse_yaml_frontmatter(text)
        assert metadata["flag1"] is True
        assert metadata["flag2"] is True
        assert metadata["flag3"] is True
        assert metadata["flag4"] is True

    def test_boolean_false_variants(self):
        text = "---\nflag1: false\nflag2: no\n---\nBody"
        metadata, _ = _parse_yaml_frontmatter(text)
        assert metadata["flag1"] is False
        assert metadata["flag2"] is False

    def test_numeric_values(self):
        text = "---\ncount: 42\nratio: 3.14\n---\nBody"
        metadata, _ = _parse_yaml_frontmatter(text)
        assert metadata["count"] == 42
        assert isinstance(metadata["count"], int)
        assert metadata["ratio"] == 3.14
        assert isinstance(metadata["ratio"], float)


class TestExtractSection:
    """Tests for section extraction from Markdown."""

    def test_extract_setup(self):
        _, body = _parse_yaml_frontmatter(SAMPLE_SKILL)
        section = _extract_section(body, "Setup")
        assert section is not None
        assert "Step one" in section
        assert "Step three" in section

    def test_extract_constraints(self):
        _, body = _parse_yaml_frontmatter(SAMPLE_SKILL)
        section = _extract_section(body, "Constraints")
        assert section is not None
        assert "Rule alpha" in section

    def test_extract_execution(self):
        _, body = _parse_yaml_frontmatter(SAMPLE_SKILL)
        section = _extract_section(body, "Execution")
        assert section is not None
        assert "Do thing A" in section

    def test_extract_output_format(self):
        _, body = _parse_yaml_frontmatter(SAMPLE_SKILL)
        section = _extract_section(body, "Output Format")
        assert section is not None
        assert "status" in section

    def test_missing_section_returns_none(self):
        _, body = _parse_yaml_frontmatter(SAMPLE_SKILL)
        assert _extract_section(body, "Nonexistent") is None

    def test_case_insensitive(self):
        text = "## setup\n\nContent here.\n\n## Other\n\nMore."
        section = _extract_section(text, "Setup")
        assert section is not None
        assert "Content here" in section


class TestExtractListItems:
    """Tests for list item extraction."""

    def test_unordered_list(self):
        items = _extract_list_items("- alpha\n- beta\n- gamma")
        assert items == ["alpha", "beta", "gamma"]

    def test_ordered_list(self):
        items = _extract_list_items("1. first\n2. second\n3. third")
        assert items == ["first", "second", "third"]

    def test_mixed_content(self):
        items = _extract_list_items("Some intro text\n\n1. item\n\nMore text\n- another")
        assert "item" in items
        assert "another" in items

    def test_none_input(self):
        assert _extract_list_items(None) == []

    def test_empty_string(self):
        assert _extract_list_items("") == []


class TestLoadSkill:
    """Tests for load_skill() with real and synthetic files."""

    def test_load_valid_skill(self, rules_env):
        gd = rules_env["global_dir"]
        skill_path = str(gd / "test_skill.md")
        (gd / "test_skill.md").write_text(SAMPLE_SKILL, encoding="utf-8")

        result = load_skill(skill_path)
        assert result["name"] == "Test Skill"
        assert result["version"] == "2.0"
        assert result["mode"] == "design"
        assert result["autonomous"] is True
        assert len(result["setup"]) == 3
        assert "Step one" in result["setup"][0]
        assert len(result["constraints"]) == 3
        assert "Rule alpha" in result["constraints"][0]
        assert "Do thing A" in result["execution"]
        assert "status" in result["output_format"]
        assert result["raw"] == SAMPLE_SKILL

    def test_load_skill_missing_sections(self, rules_env):
        gd = rules_env["global_dir"]
        (gd / "bare.md").write_text(SKILL_NO_SECTIONS, encoding="utf-8")

        result = load_skill(str(gd / "bare.md"))
        assert result["name"] == "Bare Skill"
        assert result["autonomous"] is False
        assert result["setup"] == []
        assert result["constraints"] == []
        assert result["execution"] == ""
        assert result["output_format"] == ""

    def test_load_skill_nonexistent_file(self, rules_env):
        result = load_skill("/nonexistent/path/skill.md")
        assert result["name"] == ""
        assert result["autonomous"] is False
        assert result["raw"] == ""

    @pytest.mark.skipif(
        os.environ.get("CI") == "true",
        reason="TASK-152: Skipped in CI -- relies on project filesystem layout",
    )
    def test_load_real_skill_file(self):
        """Load the example skill shipped with the project."""
        import ai.rules_loader as mod
        skill_path = os.path.join(mod.PROJECT_ROOT, "config", "rules", "fusion_design_iteration.md")
        if os.path.exists(skill_path):
            result = load_skill(skill_path)
            assert result["name"] == "Fusion Design Iteration"
            assert result["autonomous"] is True
            assert len(result["setup"]) >= 1
            assert len(result["constraints"]) >= 1
            assert result["execution"] != ""

    def test_load_skill_loop_section_alias(self, rules_env):
        """'Loop' section is treated as an alias for 'Execution'."""
        gd = rules_env["global_dir"]
        skill_with_loop = """\
---
name: Loop Skill
version: 1.0
autonomous: false
---

# Loop Skill

## Loop

1. Do X
2. Do Y
"""
        (gd / "loop_skill.md").write_text(skill_with_loop, encoding="utf-8")
        result = load_skill(str(gd / "loop_skill.md"))
        assert "Do X" in result["execution"]


class TestListSkills:
    """Tests for list_skills() finding autonomous skills."""

    def test_finds_autonomous_skills(self, rules_env):
        gd = rules_env["global_dir"]
        (gd / "auto_skill.md").write_text(SAMPLE_SKILL, encoding="utf-8")
        (gd / "manual_skill.md").write_text(SKILL_NO_SECTIONS, encoding="utf-8")

        results = list_skills(directory=str(gd))
        assert len(results) == 1
        assert results[0]["name"] == "Test Skill"
        assert results[0]["autonomous"] is True

    def test_empty_directory(self, rules_env):
        gd = rules_env["global_dir"]
        assert list_skills(directory=str(gd)) == []

    def test_nonexistent_directory(self):
        assert list_skills(directory="/nonexistent/path") == []

    def test_default_directory(self, rules_env):
        """list_skills() with no args uses config/rules/ directory.

        TASK-152: Uses monkeypatched PROJECT_ROOT from the rules_env fixture
        so this test does not depend on the real filesystem layout.
        """
        gd = rules_env["global_dir"]
        (gd / "auto.md").write_text(SAMPLE_SKILL, encoding="utf-8")

        import ai.rules_loader as mod
        old_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = str(rules_env["root"])
        try:
            results = list_skills()
            assert len(results) == 1
        finally:
            mod.PROJECT_ROOT = old_root

"""
tests/test_system_prompt.py
Unit tests for ai/system_prompt.py -- system prompt builder.

Validates the build pipeline: core identity, skill document loading,
user additions, and prompt statistics.
"""

import os
import pytest
from ai.system_prompt import (
    build_system_prompt,
    get_prompt_stats,
    CORE_IDENTITY,
    SKILL_DOC_PATH,
)


class TestBuildSystemPrompt:
    """Validate the assembled system prompt content."""

    def test_contains_core_identity(self):
        """The built prompt must include the CORE_IDENTITY text."""
        prompt = build_system_prompt()
        # Check a distinctive phrase from CORE_IDENTITY
        assert "Fusion 360 AI Design Agent" in prompt
        assert "MCP (Model Context Protocol)" in prompt

    def test_loads_skill_document(self):
        """When F360_SKILL.md exists, its content should appear in the prompt."""
        if not os.path.exists(SKILL_DOC_PATH):
            pytest.skip("Skill document not found on disk")
        prompt = build_system_prompt()
        # The skill doc is wrapped under a heading
        assert "Fusion 360 Technical Reference" in prompt
        # Read a snippet from the actual file to verify inclusion
        with open(SKILL_DOC_PATH, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
        if first_line:
            assert first_line in prompt

    def test_with_user_additions(self):
        """User-supplied text should appear after the Additional Instructions heading."""
        custom = "Always use metric units and prefer fillets over chamfers."
        prompt = build_system_prompt(user_additions=custom)
        assert "Additional Instructions" in prompt
        assert custom in prompt

    def test_without_user_additions(self):
        """Empty user additions should NOT produce the Additional Instructions section."""
        prompt = build_system_prompt(user_additions="")
        assert "Additional Instructions" not in prompt

    def test_with_whitespace_only_additions(self):
        """Whitespace-only additions are treated as empty."""
        prompt = build_system_prompt(user_additions="   \n\t  ")
        assert "Additional Instructions" not in prompt


class TestGetPromptStats:
    """Validate the statistics dict returned by get_prompt_stats()."""

    def test_returns_dict(self):
        stats = get_prompt_stats()
        assert isinstance(stats, dict)

    def test_has_expected_keys(self):
        stats = get_prompt_stats()
        for key in ("total_chars", "estimated_tokens", "skill_doc_loaded", "skill_doc_chars"):
            assert key in stats, f"Missing key: {key}"

    def test_total_chars_is_positive(self):
        stats = get_prompt_stats()
        assert stats["total_chars"] > 0

    def test_estimated_tokens_is_positive(self):
        stats = get_prompt_stats()
        assert stats["estimated_tokens"] > 0

    def test_skill_doc_loaded_is_bool(self):
        assert isinstance(get_prompt_stats()["skill_doc_loaded"], bool)


class TestSkillDocumentPath:
    """Validate the SKILL_DOC_PATH constant."""

    def test_path_exists(self):
        """The skill document should be present in the repository."""
        assert os.path.exists(SKILL_DOC_PATH), (
            f"Expected skill document at {SKILL_DOC_PATH}"
        )

    def test_path_ends_with_md(self):
        assert SKILL_DOC_PATH.endswith(".md")

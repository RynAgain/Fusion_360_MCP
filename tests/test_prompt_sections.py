"""
tests/test_prompt_sections.py
Unit tests for ai/prompt_sections/ modules and the refactored build_system_prompt().
"""

import pytest
from ai.prompt_sections import (
    identity,
    capabilities,
    workflow,
    rules,
    verification,
    custom_instructions,
)
from ai.system_prompt import build_system_prompt, get_prompt_stats, CORE_IDENTITY, ORCHESTRATION_PROTOCOL


# ---------------------------------------------------------------------------
# Individual section modules
# ---------------------------------------------------------------------------

class TestIdentitySection:
    """Validate identity.build() output."""

    def test_returns_non_empty_string(self):
        result = identity.build({})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_artifex360(self):
        result = identity.build({})
        assert "Artifex360" in result

    def test_contains_autonomous_action_protocol(self):
        result = identity.build({})
        assert "Autonomous Action Protocol" in result

    def test_contains_requirements_clarification(self):
        result = identity.build({})
        assert "Requirements Clarification" in result

    def test_mode_context_adds_specialisation(self):
        """TASK-195: identity.build() customises identity when mode is provided."""
        result_sketch = identity.build({"mode": "sketch"})
        assert "2D sketch specialist" in result_sketch

        result_orchestrator = identity.build({"mode": "orchestrator"})
        assert "orchestrator" in result_orchestrator.lower()

    def test_full_mode_has_no_extra_specialisation(self):
        """TASK-195: 'full' mode should not add a specialisation label."""
        result = identity.build({"mode": "full"})
        assert "specialist" not in result.lower()
        assert "orchestrator" not in result.lower()

    def test_no_mode_has_no_extra_specialisation(self):
        """TASK-195: No mode context should not add a specialisation label."""
        result = identity.build({})
        assert "currently operating" not in result.lower()


class TestCapabilitiesSection:
    """Validate capabilities.build() output."""

    def test_returns_non_empty_string(self):
        result = capabilities.build({})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_capabilities_heading(self):
        result = capabilities.build({})
        assert "Capabilities" in result

    def test_contains_key_capabilities(self):
        result = capabilities.build({})
        assert "3D geometry" in result
        assert "execute custom Python scripts" in result
        assert "STL" in result


class TestWorkflowSection:
    """Validate workflow.build() output."""

    def test_returns_non_empty_string(self):
        result = workflow.build({})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_plan_act_verify(self):
        result = workflow.build({})
        assert "Plan-Act-Verify" in result

    def test_contains_workflow_steps(self):
        result = workflow.build({})
        assert "Clarify" in result
        assert "Verify" in result
        assert "Report" in result


class TestRulesSection:
    """Validate rules.build() output."""

    def test_returns_non_empty_string(self):
        result = rules.build({})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_centimeters(self):
        result = rules.build({})
        assert "centimeters" in result

    def test_contains_design_quality_standards(self):
        result = rules.build({})
        assert "Design Quality Standards" in result

    def test_contains_important_rules(self):
        result = rules.build({})
        assert "Important Rules" in result

    def test_sketch_mode_adds_sketch_rules(self):
        """TASK-195: rules.build() adds sketch-specific rules in sketch mode."""
        result = rules.build({"mode": "sketch"})
        assert "Sketch Mode Rules" in result
        assert "closed" in result.lower()

    def test_modeling_mode_adds_modeling_rules(self):
        """TASK-195: rules.build() adds modeling-specific rules in modeling mode."""
        result = rules.build({"mode": "modeling"})
        assert "Modeling Mode Rules" in result

    def test_orchestrator_mode_adds_orchestrator_rules(self):
        """TASK-195: rules.build() adds orchestrator-specific rules."""
        result = rules.build({"mode": "orchestrator"})
        assert "Orchestrator Mode Rules" in result

    def test_full_mode_has_no_extra_rules(self):
        """TASK-195: 'full' mode should not add mode-specific rules."""
        result = rules.build({"mode": "full"})
        assert "Mode Rules" not in result

    def test_no_mode_has_no_extra_rules(self):
        """TASK-195: No mode in context should not add mode-specific rules."""
        result = rules.build({})
        assert "Mode Rules" not in result


class TestVerificationSection:
    """Validate verification.build() output."""

    def test_returns_non_empty_string(self):
        result = verification.build({})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_verification_protocol(self):
        result = verification.build({})
        assert "Verification Protocol" in result

    def test_contains_verification_steps(self):
        result = verification.build({})
        assert "delta" in result
        assert "take_screenshot" in result


class TestCustomInstructionsSection:
    """Validate custom_instructions.build() output."""

    def test_returns_empty_for_no_additions(self):
        result = custom_instructions.build({})
        assert result == ""

    def test_returns_empty_for_empty_additions(self):
        result = custom_instructions.build({"user_additions": "", "mode_rules": ""})
        assert result == ""

    def test_returns_empty_for_whitespace_additions(self):
        result = custom_instructions.build({"user_additions": "   \n  ", "mode_rules": "  "})
        assert result == ""

    def test_includes_user_additions(self):
        ctx = {"user_additions": "Always use metric units."}
        result = custom_instructions.build(ctx)
        assert "User Instructions" in result
        assert "Always use metric units." in result

    def test_includes_mode_rules(self):
        ctx = {"mode_rules": "Focus on sketch constraints."}
        result = custom_instructions.build(ctx)
        assert "Mode-Specific Rules" in result
        assert "Focus on sketch constraints." in result

    def test_includes_both_user_and_mode(self):
        ctx = {
            "user_additions": "Use imperial.",
            "mode_rules": "Sketch mode only.",
        }
        result = custom_instructions.build(ctx)
        assert "User Instructions" in result
        assert "Mode-Specific Rules" in result
        assert "Use imperial." in result
        assert "Sketch mode only." in result


# ---------------------------------------------------------------------------
# Integrated build_system_prompt() tests
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:
    """Validate the fully assembled system prompt."""

    def test_produces_non_empty_output(self):
        prompt = build_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_includes_identity_section(self):
        prompt = build_system_prompt()
        assert "Artifex360" in prompt
        assert "Autonomous Action Protocol" in prompt

    def test_includes_capabilities(self):
        prompt = build_system_prompt()
        assert "Capabilities" in prompt

    def test_includes_workflow(self):
        prompt = build_system_prompt()
        assert "Plan-Act-Verify" in prompt

    def test_includes_rules(self):
        prompt = build_system_prompt()
        assert "centimeters" in prompt
        assert "Design Quality Standards" in prompt

    def test_includes_verification_protocol(self):
        prompt = build_system_prompt()
        assert "Verification Protocol" in prompt

    def test_includes_error_recovery(self):
        prompt = build_system_prompt()
        assert "Error Recovery" in prompt

    def test_with_user_additions(self):
        custom = "Always use metric units and prefer fillets over chamfers."
        prompt = build_system_prompt(user_additions=custom)
        assert "Additional Instructions" in prompt
        assert custom in prompt

    def test_without_user_additions(self):
        prompt = build_system_prompt(user_additions="")
        assert "Additional Instructions" not in prompt

    def test_orchestration_in_orchestrator_mode(self):
        prompt = build_system_prompt(mode="orchestrator")
        assert "Orchestration Protocol" in prompt

    def test_orchestration_not_in_other_modes(self):
        prompt = build_system_prompt(mode="sketch")
        assert "Orchestration Protocol" not in prompt

    def test_orchestration_not_in_default_mode(self):
        prompt = build_system_prompt()
        assert "Orchestration Protocol" not in prompt

    def test_sketch_mode_includes_identity_specialisation(self):
        """TASK-195: build_system_prompt passes mode to identity section."""
        prompt = build_system_prompt(mode="sketch")
        assert "2D sketch specialist" in prompt

    def test_sketch_mode_includes_sketch_rules(self):
        """TASK-195: build_system_prompt passes mode to rules section."""
        prompt = build_system_prompt(mode="sketch")
        assert "Sketch Mode Rules" in prompt


# ---------------------------------------------------------------------------
# Legacy constant backward compatibility
# ---------------------------------------------------------------------------

class TestLegacyConstants:
    """Verify backward-compatible constants are still importable."""

    def test_core_identity_constant_exists(self):
        assert isinstance(CORE_IDENTITY, str)
        assert len(CORE_IDENTITY.strip()) > 0
        assert "Artifex360" in CORE_IDENTITY

    def test_orchestration_protocol_constant_exists(self):
        assert isinstance(ORCHESTRATION_PROTOCOL, str)
        assert len(ORCHESTRATION_PROTOCOL.strip()) > 0
        assert "Orchestration Protocol" in ORCHESTRATION_PROTOCOL


# ---------------------------------------------------------------------------
# get_prompt_stats()
# ---------------------------------------------------------------------------

class TestGetPromptStats:
    """Validate get_prompt_stats() still works after refactor."""

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

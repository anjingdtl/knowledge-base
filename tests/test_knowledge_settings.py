"""Runtime-effective knowledge settings must have one compatibility contract."""
from __future__ import annotations

from copy import deepcopy

import pytest

from src.utils.knowledge_mode import InvalidKnowledgeModeError
from src.utils.knowledge_settings import resolve_effective_knowledge_settings


@pytest.mark.parametrize(
    ("config", "mode", "wiki_read", "authoring", "hybrid", "write_policy"),
    [
        ({}, "verified", True, False, True, "disabled"),
        ({"knowledge_workflow": {"mode": "verified"}}, "verified", True, False, True, "disabled"),
        ({"knowledge_workflow": {"mode": "authoring"}}, "authoring", True, True, True, "local_confirm"),
        ({"knowledge_workflow": {"mode": "evidence_only"}}, "evidence_only", False, False, False, "disabled"),
        ({"knowledge_workflow": {"mode": "wiki_first"}}, "authoring", True, True, True, "local_confirm"),
        ({"knowledge_workflow": {"mode": "legacy"}}, "evidence_only", False, False, False, "disabled"),
    ],
)
def test_resolves_mode_defaults_without_mutating_input(
    config, mode, wiki_read, authoring, hybrid, write_policy,
):
    before = deepcopy(config)

    settings = resolve_effective_knowledge_settings(config)

    assert settings.mode == mode
    assert settings.wiki_read_enabled is wiki_read
    assert settings.authoring_enabled is authoring
    assert settings.verified_hybrid_enabled is hybrid
    assert settings.mcp_write_policy == write_policy
    assert config == before


def test_wiki_first_missing_verified_switch_still_enables_hybrid():
    settings = resolve_effective_knowledge_settings({
        "knowledge_workflow": {"mode": "wiki_first"},
        "mcp": {"write_policy": "token_required"},
        "custom": {"keep": "unchanged"},
    })

    assert settings.mode == "authoring"
    assert settings.verified_hybrid_enabled is True
    assert settings.mcp_write_policy == "token_required"
    assert "legacy mode" in " ".join(settings.compatibility_warnings).lower()
    assert settings.sources["verified_hybrid_enabled"] == "derived:mode"


def test_existing_mcp_config_without_profile_keeps_legacy_surface():
    settings = resolve_effective_knowledge_settings({
        "mcp": {"write_policy": "local_confirm"},
    })

    assert settings.mcp_tool_profile == "legacy"
    assert settings.mcp_write_policy == "local_confirm"


def test_explicit_flags_win_over_mode_defaults():
    settings = resolve_effective_knowledge_settings({
        "knowledge_workflow": {"mode": "authoring"},
        "wiki": {"read_enabled": False, "authoring_enabled": False},
        "rag": {"verified_knowledge": {"enabled": False}},
        "maintenance": {"enabled": False},
        "mcp": {"tool_profile": "core", "write_policy": "disabled", "allow_http_write": True},
        "wiki.canonical_v2.mode": "shadow",
    })

    assert settings.wiki_read_enabled is False
    assert settings.authoring_enabled is False
    assert settings.verified_hybrid_enabled is False
    assert settings.maintenance_enabled is False
    assert settings.mcp_tool_profile == "core"
    assert settings.mcp_write_policy == "disabled"
    assert settings.allow_http_write is True
    assert settings.canonical_write_mode == "shadow"
    assert settings.sources["verified_hybrid_enabled"] == "explicit:rag.verified_knowledge.enabled"


def test_invalid_mode_is_not_silently_treated_as_verified():
    with pytest.raises(InvalidKnowledgeModeError):
        resolve_effective_knowledge_settings({"knowledge_workflow": {"mode": "turbo"}})


def test_boolean_yaml_off_for_canonical_mode_is_compatibly_normalized_with_warning():
    settings = resolve_effective_knowledge_settings({"wiki": {"canonical_v2": {"mode": False}}})

    assert settings.canonical_write_mode == "off"
    assert any("boolean" in warning.lower() for warning in settings.compatibility_warnings)

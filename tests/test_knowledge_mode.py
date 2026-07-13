"""Phase 1: knowledge_workflow mode resolution tests."""
from __future__ import annotations

import pytest

from src.utils.knowledge_mode import (
    MODE_AUTHORING,
    MODE_EVIDENCE_ONLY,
    MODE_VERIFIED,
    InvalidKnowledgeModeError,
    allows_authoring,
    allows_wiki_read,
    describe_mode,
    is_evidence_only,
    is_legacy_mode_alias,
    resolve_knowledge_mode,
)


class TestResolveKnowledgeMode:
    def test_default_verified(self):
        assert resolve_knowledge_mode(None) == MODE_VERIFIED
        assert resolve_knowledge_mode("") == MODE_VERIFIED

    def test_canonical_modes(self):
        assert resolve_knowledge_mode("verified") == MODE_VERIFIED
        assert resolve_knowledge_mode("authoring") == MODE_AUTHORING
        assert resolve_knowledge_mode("evidence_only") == MODE_EVIDENCE_ONLY

    def test_cli_hyphen_evidence_only(self):
        assert resolve_knowledge_mode("evidence-only") == MODE_EVIDENCE_ONLY

    def test_legacy_wiki_first_maps_to_authoring(self):
        assert resolve_knowledge_mode("wiki_first") == MODE_AUTHORING
        assert is_legacy_mode_alias("wiki_first") is True

    def test_legacy_maps_to_evidence_only(self):
        assert resolve_knowledge_mode("legacy") == MODE_EVIDENCE_ONLY
        assert is_legacy_mode_alias("legacy") is True

    def test_case_and_space_insensitive(self):
        assert resolve_knowledge_mode("  Authoring ") == MODE_AUTHORING
        assert resolve_knowledge_mode("WIKI_FIRST") == MODE_AUTHORING

    def test_invalid_raises(self):
        with pytest.raises(InvalidKnowledgeModeError):
            resolve_knowledge_mode("turbo_mode")


class TestModeCapabilities:
    def test_authoring_flags(self):
        assert allows_authoring("authoring") is True
        assert allows_authoring("wiki_first") is True
        assert allows_authoring("verified") is False
        assert allows_authoring("legacy") is False

    def test_wiki_read_flags(self):
        assert allows_wiki_read("verified") is True
        assert allows_wiki_read("authoring") is True
        assert allows_wiki_read("evidence_only") is False
        assert allows_wiki_read("legacy") is False

    def test_evidence_only(self):
        assert is_evidence_only("evidence_only") is True
        assert is_evidence_only("legacy") is True
        assert is_evidence_only("verified") is False

    def test_describe_deprecation(self):
        info = describe_mode("wiki_first")
        assert info["resolved"] == MODE_AUTHORING
        assert info["legacy_alias"] is True
        assert info["deprecation_hint"]
        assert info["authoring_enabled"] is True

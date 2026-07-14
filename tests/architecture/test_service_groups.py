"""Capability Provider tests (WP3)."""
from unittest.mock import MagicMock

import pytest

from src.core.service_groups import (
    FeatureDisabledError,
    ServiceGroups,
)


def test_service_groups_providers_delegate_shared_infra():
    c = MagicMock()
    c.config.get = MagicMock(
        side_effect=lambda key, default=None: {
            "wiki.authoring_enabled": True,
            "mcp.experimental_tools_enabled": True,
            "knowledge_mode": "authoring",
        }.get(key, default),
    )
    c.db = "db"
    c.vectorstore = "vs"
    c.block_store = "bs"
    c.embedding = "emb"
    c.llm = "llm"
    c.knowledge_repo = "kr"
    c.block_repo = "br"
    c.graph_backend = "gb"
    c.agent_memory_repo = "amr"
    c.indexed_file_repo = "ifr"
    c._track_service = MagicMock()

    g = ServiceGroups(c)
    assert g.core.db == "db"
    assert g.core.knowledge_repo == "kr"
    assert g.experimental.enabled is True
    assert g.authoring.write_enabled is True
    assert g.experimental.graph_backend == "gb"


def test_authoring_write_gate_blocks_when_disabled():
    c = MagicMock()
    c.config.get = MagicMock(
        side_effect=lambda key, default=None: {
            "wiki.authoring_enabled": False,
            "mcp.experimental_tools_enabled": False,
            "knowledge_mode": "verified",
        }.get(key, default),
    )
    c._track_service = MagicMock()
    g = ServiceGroups(c)
    assert g.authoring.write_enabled is False
    with pytest.raises(FeatureDisabledError):
        _ = g.authoring.wiki_write_service
    with pytest.raises(FeatureDisabledError):
        _ = g.experimental.agent_memory


def test_provider_close_is_idempotent():
    c = MagicMock()
    c.config.get = MagicMock(return_value=False)
    c._track_service = MagicMock()
    g = ServiceGroups(c)
    g.close()
    g.close()  # must not raise

"""WP3: capability provider isolation and lifecycle (uses setup_db fixture)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.compatibility.container_access import get_active_container, set_active_container
from src.core.container import create_container, shutdown_container
from src.core.service_groups import FeatureDisabledError, ServiceGroups
from src.utils.config import Config


def _rebind_groups(c) -> None:
    """Force ServiceGroups rebuild after Config.set (create_container reloads yaml)."""
    Config.set("wiki.authoring_enabled", False)
    Config.set("mcp.experimental_tools_enabled", False)
    Config.set("knowledge_mode", "verified")
    c._service_groups = None


def test_core_access_does_not_construct_experimental():
    c = create_container()
    try:
        _rebind_groups(c)
        _ = c.groups.core.db
        _ = c.groups.core.search_service
        assert "graph_builder" not in c.groups.experimental.constructed_keys
        assert "agent_memory" not in c.groups.experimental.constructed_keys
        assert "wiki_write_service" not in c.groups.authoring.constructed_keys
    finally:
        # Do not close db — setup_db owns Database._instance for the test session
        groups = getattr(c, "_service_groups", None)
        if groups is not None:
            groups.close()
        set_active_container(None)


def test_experimental_disabled_raises_on_group_access():
    c = create_container()
    try:
        _rebind_groups(c)
        assert c.groups.experimental.enabled is False
        with pytest.raises(FeatureDisabledError):
            _ = c.groups.experimental.agent_memory
        with pytest.raises(FeatureDisabledError):
            _ = c.groups.experimental.graph_builder
        # Flat compat still works for one release cycle
        assert c.agent_memory is not None
    finally:
        groups = getattr(c, "_service_groups", None)
        if groups is not None:
            groups.close()
        set_active_container(None)


def test_authoring_disabled_blocks_group_writes():
    c = create_container()
    try:
        _rebind_groups(c)
        assert c.groups.authoring.write_enabled is False
        with pytest.raises(FeatureDisabledError):
            _ = c.groups.authoring.wiki_write_service
        assert c.groups.verified.wiki_serving_gate is not None
        assert c.groups.authoring.wiki_projection is not None
    finally:
        groups = getattr(c, "_service_groups", None)
        if groups is not None:
            groups.close()
        set_active_container(None)


def test_provider_close_idempotent_and_clears_active():
    c = create_container()
    assert get_active_container() is c
    _ = c.groups.core.search_service
    groups = c.groups
    groups.close()
    groups.close()  # idempotent
    set_active_container(None)
    assert get_active_container() is None


def test_two_provider_graphs_are_independent():
    """Two ServiceGroups instances keep separate caches (multi-container isolation)."""
    base = MagicMock()
    base.config.get = MagicMock(
        side_effect=lambda key, default=None: {
            "wiki.authoring_enabled": True,
            "mcp.experimental_tools_enabled": True,
            "knowledge_mode": "authoring",
        }.get(key, default),
    )
    base.db = object()
    base.vectorstore = object()
    base.block_store = object()
    base.embedding = object()
    base.llm = object()
    base.knowledge_repo = object()
    base.block_repo = object()
    base.graph_backend = object()
    base.agent_memory_repo = object()
    base.indexed_file_repo = object()
    base._track_service = MagicMock()
    base.wiki_compiler = object()
    base.knowledge_workflow = object()
    base.wiki_dependency_service = object()
    base.wiki_feedback_service = object()
    base.operation_log = object()

    g1 = ServiceGroups(base)
    g2 = ServiceGroups(base)
    assert g1 is not g2
    assert g1.core is not g2.core
    # Construct on g1 only
    built = object()

    def _factory():
        return built

    g1.core._lazy("marker", _factory)
    assert g1.core.has_constructed("marker")
    assert not g2.core.has_constructed("marker")


def test_active_container_set_on_create():
    c = create_container()
    try:
        assert get_active_container() is c
    finally:
        set_active_container(None)

"""Capability Providers — own construction and lifecycle (WP3).

Groups:
  CoreEvidence — db, retrieval, indexing (always on)
  VerifiedServing — wiki read, gate, verified serving
  Authoring — claim write / projection / maintenance (gated by wiki.authoring_enabled)
  Experimental — graph services, agent memory (gated by mcp.experimental_tools_enabled)

AppContainer flat properties remain compatibility proxies for one release cycle.
New code should prefer ``container.groups.core.*`` / ``.verified.*`` etc.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, cast

if TYPE_CHECKING:
    from src.core.container import AppContainer

logger = logging.getLogger(__name__)


class FeatureDisabledError(RuntimeError):
    """Raised when accessing a capability that is config-disabled."""


class _LazyProviderBase:
    """Shared lazy cache + close semantics for capability providers."""

    def __init__(self, container: "AppContainer") -> None:
        self._c = container
        self._cache: dict[str, Any] = {}
        self._init_order: list[str] = []
        self._closed = False

    def _lazy(self, key: str, factory: Callable[[], Any]) -> Any:
        if self._closed:
            raise RuntimeError(f"Provider already closed; cannot access {key!r}")
        if key not in self._cache:
            self._cache[key] = factory()
            self._init_order.append(key)
            # Keep container tracking for shutdown_container compatibility
            track = getattr(self._c, "_track_service", None)
            if callable(track):
                track(f"_{key}")
        return self._cache[key]

    def has_constructed(self, key: str) -> bool:
        return key in self._cache

    @property
    def constructed_keys(self) -> tuple[str, ...]:
        return tuple(self._init_order)

    def close(self) -> None:
        if self._closed:
            return
        for key in reversed(self._init_order):
            svc = self._cache.get(key)
            if svc is not None and hasattr(svc, "close"):
                try:
                    svc.close()
                except Exception:  # noqa: BLE001
                    logger.debug("close failed for %s", key, exc_info=True)
        self._cache.clear()
        self._init_order.clear()
        self._closed = True


class CoreEvidenceProvider(_LazyProviderBase):
    """Core evidence stack: storage handles + search + path indexing."""

    @property
    def config(self) -> Any:
        return self._c.config

    @property
    def db(self) -> Any:
        return self._c.db

    @property
    def vectorstore(self) -> Any:
        return self._c.vectorstore

    @property
    def block_store(self) -> Any:
        return self._c.block_store

    @property
    def embedding(self) -> Any:
        return self._c.embedding

    @property
    def llm(self) -> Any:
        return self._c.llm

    @property
    def knowledge_repo(self) -> Any:
        return self._c.knowledge_repo

    @property
    def block_repo(self) -> Any:
        return self._c.block_repo

    @property
    def search_service(self) -> Any:
        return self._lazy("search_service", self._build_search_service)

    @property
    def path_indexer(self) -> Any:
        return self._lazy("path_indexer", self._build_path_indexer)

    def _build_search_service(self) -> Any:
        from src.services.search_service import SearchService

        c = self._c
        # Verified deps are optional construction: do not force authoring.
        wiki_repo = None
        wiki_gate = None
        try:
            wiki_repo = c.groups.verified.wiki_repository
            wiki_gate = c.groups.verified.wiki_serving_gate
        except Exception as e:  # noqa: BLE001
            logger.warning("SearchService without verified wiki deps: %s", e)
        return SearchService(
            c.config,
            c.db,
            c.block_store,
            c.embedding,
            c.llm,
            wiki_repository=wiki_repo,
            wiki_serving_gate=wiki_gate,
        )

    def _build_path_indexer(self) -> Any:
        from src.services.path_indexer import PathIndexService

        c = self._c
        return PathIndexService(
            db=c.db,
            config=c.config,
            indexed_file_repo=c.indexed_file_repo,
            # Lazy providers avoid Core→Authoring circular construction at boot.
            knowledge_workflow_provider=lambda: c.knowledge_workflow,
            maintenance_event_adapter_provider=lambda: c.maintenance_event_adapter,
        )


class VerifiedServingProvider(_LazyProviderBase):
    """Wiki read / serving gate path (must not require authoring writes)."""

    @property
    def wiki_repository(self) -> Any:
        return self._lazy("wiki_repository", self._build_wiki_repository)

    @property
    def wiki_serving_gate(self) -> Any:
        return self._lazy("wiki_serving_gate", self._build_wiki_serving_gate)

    @property
    def wiki_query_service(self) -> Any:
        return self._lazy("wiki_query_service", self._build_wiki_query_service)

    def _build_wiki_repository(self) -> Any:
        from pathlib import Path as _Path

        from src.services.wiki_repository import WikiRepository as _WikiRepo

        c = self._c
        wiki_dir = c.config.get("knowledge_workflow.wiki_dir", "wiki")
        wiki_dir_path = _Path(wiki_dir)
        return _WikiRepo(
            wiki_dir=wiki_dir_path,
            registry_path=wiki_dir_path / "_meta" / "pages.json",
            redirects_path=wiki_dir_path / "_meta" / "redirects.json",
            outbox_path=_Path(c.config.get("storage.data_dir", "data"))
            / "wiki_projection_outbox.jsonl",
        )

    def _build_wiki_serving_gate(self) -> Any:
        from src.services.wiki_serving_gate import (
            WikiServingGate,
            default_block_knowledge_lookups,
        )

        c = self._c
        get_block, get_knowledge = default_block_knowledge_lookups()
        return WikiServingGate(
            config=c.config.get_all() if hasattr(c.config, "get_all") else None,
            get_block=get_block,
            get_knowledge=get_knowledge,
        )

    def _build_wiki_query_service(self) -> Any:
        from src.services.wiki_query_service import WikiQueryService as _QS

        c = self._c
        # Projection is shared read infrastructure (not gated as a write).
        projection = c.groups.authoring.wiki_projection
        return _QS(
            repository=self.wiki_repository,
            projection=projection,
            database=c.db,
            config=c.config,
        )


class AuthoringProvider(_LazyProviderBase):
    """Claim write / projection / maintenance. Write surface gated by config."""

    def __init__(self, container: "AppContainer", *, write_enabled: bool) -> None:
        super().__init__(container)
        self.write_enabled = write_enabled

    def _require_write(self, name: str) -> None:
        if not self.write_enabled:
            raise FeatureDisabledError(
                f"Authoring write disabled; cannot access {name}. "
                "Enable wiki.authoring_enabled / knowledge_mode=authoring.",
            )

    @property
    def wiki_projection(self) -> Any:
        # Read-side projection remains available for Serving even when writes off.
        return self._lazy("wiki_projection", self._build_wiki_projection)

    @property
    def maintenance_policy(self) -> Any:
        return self._lazy("maintenance_policy", self._build_maintenance_policy)

    @property
    def wiki_claim_extractor(self) -> Any:
        self._require_write("wiki_claim_extractor")
        return self._lazy("wiki_claim_extractor", self._build_wiki_claim_extractor)

    @property
    def wiki_write_service(self) -> Any:
        self._require_write("wiki_write_service")
        return self._lazy("wiki_write_service", self._build_wiki_write_service)

    @property
    def wiki_maintenance_service(self) -> Any:
        self._require_write("wiki_maintenance_service")
        return self._lazy(
            "wiki_maintenance_service", self._build_wiki_maintenance_service,
        )

    @property
    def wiki_rebuild_service(self) -> Any:
        # Rebuild supports Serving recovery; allow when write off but only via
        # flat proxy path for ops; gate group write-ish access when disabled.
        if not self.write_enabled and not self.has_constructed("wiki_rebuild_service"):
            # Still construct for flat-compat callers that need rebuild read.
            pass
        return self._lazy("wiki_rebuild_service", self._build_wiki_rebuild_service)

    def _build_wiki_projection(self) -> Any:
        from src.services.wiki_projection import WikiProjection as _Proj
        from src.services.wiki_query_service import resolve_canonical_mode

        c = self._c
        enabled = resolve_canonical_mode(c.config) != "off"
        return _Proj(
            repository=c.groups.verified.wiki_repository,
            database=c.db,
            enabled=enabled,
        )

    def _build_maintenance_policy(self) -> Any:
        from src.services.maintenance_policy import MaintenancePolicyEngine

        return MaintenancePolicyEngine(cast(Any, self._c.config))

    def _build_wiki_claim_extractor(self) -> Any:
        from src.services.wiki_claim_extractor import ClaimExtractor as _Ext

        c = self._c
        return _Ext(llm=c.llm, config=c.config)

    def _build_wiki_write_service(self) -> Any:
        from src.services.wiki_write_service import WikiWriteService

        c = self._c
        return WikiWriteService(
            wiki_compiler=c.wiki_compiler,
            knowledge_workflow=c.knowledge_workflow,
            repository=c.groups.verified.wiki_repository,
            projection=self.wiki_projection,
            config=c.config,
        )

    def _build_wiki_maintenance_service(self) -> Any:
        from src.services.wiki_maintenance_service import WikiMaintenanceService

        c = self._c
        try:
            return WikiMaintenanceService(
                config=c.config,
                policy_engine=self.maintenance_policy,
                wiki_repository=c.groups.verified.wiki_repository,
                rebuild_service=self.wiki_rebuild_service,
                dependency_service=c.wiki_dependency_service,
                feedback_service=c.wiki_feedback_service,
                operation_log=c.operation_log,
                wiki_serving_gate=c.groups.verified.wiki_serving_gate,
                projection=self.wiki_projection,
                db=c.db,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "WikiMaintenanceService init failed (Raw search unaffected): %s", e,
            )
            return WikiMaintenanceService(
                config=c.config,
                policy_engine=self.maintenance_policy,
            )

    def _build_wiki_rebuild_service(self) -> Any:
        from src.services.wiki_rebuild_service import WikiRebuildService as _RB

        c = self._c
        return _RB(
            repository=c.groups.verified.wiki_repository,
            projection=self.wiki_projection,
            block_repository=c.block_repo,
            dependency_service=c.wiki_dependency_service,
            config=c.config,
        )


class ExperimentalProvider(_LazyProviderBase):
    """Graph / agent memory — only when experimental tools enabled."""

    def __init__(self, container: "AppContainer", *, enabled: bool) -> None:
        super().__init__(container)
        self.enabled = enabled

    def _require_enabled(self, name: str) -> None:
        if not self.enabled:
            raise FeatureDisabledError(
                f"Experimental capability disabled; cannot access {name}. "
                "Set mcp.experimental_tools_enabled=true.",
            )

    @property
    def graph_backend(self) -> Any:
        # Always available as shared infra created at boot (SQLite backend).
        return self._c.graph_backend

    @property
    def graph_builder(self) -> Any:
        self._require_enabled("graph_builder")
        return self._lazy("graph_builder", self._build_graph_builder)

    @property
    def unified_graph(self) -> Any:
        self._require_enabled("unified_graph")
        return self._lazy("unified_graph", self._build_unified_graph)

    @property
    def agent_memory(self) -> Any:
        self._require_enabled("agent_memory")
        return self._lazy("agent_memory", self._build_agent_memory)

    def _build_graph_builder(self) -> Any:
        from src.services.graph_builder import GraphBuilder

        return GraphBuilder(graph_backend=self._c.graph_backend)

    def _build_unified_graph(self) -> Any:
        from src.services.unified_graph import UnifiedGraphService

        c = self._c
        return UnifiedGraphService(db=c.db, graph_backend=c.graph_backend)

    def _build_agent_memory(self) -> Any:
        from src.services.agent_memory import AgentMemoryService

        c = self._c
        return AgentMemoryService(
            repo=c.agent_memory_repo,
            db=c.db,
            llm=c.llm,
        )


class ServiceGroups:
    """Capability groups bound to an AppContainer; providers own construction."""

    def __init__(self, container: "AppContainer") -> None:
        self._c = container
        cfg = container.config
        write_enabled = bool(cfg.get("wiki.authoring_enabled", False))
        # knowledge_mode=authoring also implies write
        mode = str(cfg.get("knowledge_mode", "") or "").strip().lower()
        if mode == "authoring":
            write_enabled = True
        experimental = bool(cfg.get("mcp.experimental_tools_enabled", False))

        self.core = CoreEvidenceProvider(container)
        self.verified = VerifiedServingProvider(container)
        self.authoring = AuthoringProvider(container, write_enabled=write_enabled)
        self.experimental = ExperimentalProvider(container, enabled=experimental)

    def close(self) -> None:
        """Close only providers that constructed services (idempotent)."""
        for prov in (
            self.experimental,
            self.authoring,
            self.verified,
            self.core,
        ):
            prov.close()


# Back-compat aliases for older import sites / type docs
CoreEvidenceServices = CoreEvidenceProvider
VerifiedServingServices = VerifiedServingProvider
AuthoringServices = AuthoringProvider
ExperimentalServices = ExperimentalProvider

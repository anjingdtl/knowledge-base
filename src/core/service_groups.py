"""Capability-grouped service views over AppContainer (Phase-3).

Groups:
  CoreEvidence — db, retrieval, answer, indexing
  VerifiedServing — wiki read, gate, verified provider
  Authoring — claim write, projection, maintenance
  Experimental — graph, agent memory, plugins

AppContainer keeps flat lazy properties for backward compatibility; these
groups are read-only views that do not own lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.container import AppContainer


@dataclass(frozen=True)
class CoreEvidenceServices:
    config: Any
    db: Any
    vectorstore: Any
    block_store: Any
    embedding: Any
    llm: Any
    knowledge_repo: Any
    block_repo: Any
    search_service: Any
    path_indexer: Any
    citation_note: str = "CitationBuilder is constructed per-request from db"


@dataclass(frozen=True)
class VerifiedServingServices:
    wiki_repository: Any
    wiki_serving_gate: Any
    wiki_query_service: Any
    note: str = "VerifiedProvider is constructed per-request from wiki_repository + gate"


@dataclass(frozen=True)
class AuthoringServices:
    wiki_write_service: Any
    wiki_claim_extractor: Any
    wiki_projection: Any
    wiki_maintenance_service: Any
    wiki_rebuild_service: Any
    maintenance_policy: Any


@dataclass(frozen=True)
class ExperimentalServices:
    graph_backend: Any
    graph_builder: Any
    unified_graph: Any
    agent_memory: Any


class ServiceGroups:
    """Lazy capability views bound to an AppContainer instance."""

    def __init__(self, container: "AppContainer"):
        self._c = container

    @property
    def core(self) -> CoreEvidenceServices:
        c = self._c
        return CoreEvidenceServices(
            config=c.config,
            db=c.db,
            vectorstore=c.vectorstore,
            block_store=c.block_store,
            embedding=c.embedding,
            llm=c.llm,
            knowledge_repo=c.knowledge_repo,
            block_repo=c.block_repo,
            search_service=c.search_service,
            path_indexer=c.path_indexer,
        )

    @property
    def verified(self) -> VerifiedServingServices:
        c = self._c
        return VerifiedServingServices(
            wiki_repository=c.wiki_repository,
            wiki_serving_gate=c.wiki_serving_gate,
            wiki_query_service=c.wiki_query_service,
        )

    @property
    def authoring(self) -> AuthoringServices:
        c = self._c
        return AuthoringServices(
            wiki_write_service=c.wiki_write_service,
            wiki_claim_extractor=c.wiki_claim_extractor,
            wiki_projection=c.wiki_projection,
            wiki_maintenance_service=c.wiki_maintenance_service,
            wiki_rebuild_service=c.wiki_rebuild_service,
            maintenance_policy=c.maintenance_policy,
        )

    @property
    def experimental(self) -> ExperimentalServices:
        c = self._c
        return ExperimentalServices(
            graph_backend=c.graph_backend,
            graph_builder=c.graph_builder,
            unified_graph=c.unified_graph,
            agent_memory=c.agent_memory,
        )

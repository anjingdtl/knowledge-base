"""图后端工厂 — 根据配置创建对应的 GraphBackend 实例"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.services.graph_backend.base import GraphBackend

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def create_graph_backend(config, db=None) -> GraphBackend:
    """根据 config.yaml 中的 graph_backend 配置创建图后端实例

    配置格式 (config.yaml):
        graph_backend:
          provider: sqlite          # sqlite | neo4j
          # Neo4j 专用配置:
          uri: bolt://localhost:7687
          user: neo4j
          password: ""
          database: neo4j
          max_connection_pool_size: 50

    如果未配置 graph_backend 节点，默认使用 SQLite 后端。
    """
    from src.services.db import Database

    db = db or Database
    provider = config.get("graph_backend.provider", "sqlite")

    if provider == "sqlite":
        from src.services.graph_backend.sqlite_backend import SQLiteGraphBackend
        backend: GraphBackend = SQLiteGraphBackend(db=db)
        logger.info("Graph backend: SQLite (default)")
        return backend

    if provider == "neo4j":
        try:
            from src.services.graph_backend.neo4j_backend import Neo4jGraphBackend
            backend = Neo4jGraphBackend(
                uri=config.get("graph_backend.uri", "bolt://localhost:7687"),
                user=config.get("graph_backend.user", "neo4j"),
                password=config.get("graph_backend.password", ""),
                database=config.get("graph_backend.database", "neo4j"),
                max_connection_pool_size=int(
                    config.get("graph_backend.max_connection_pool_size", 50)
                ),
            )
            logger.info("Graph backend: Neo4j (%s)", config.get("graph_backend.uri", "bolt://localhost:7687"))
            return backend
        except (ImportError, Exception) as e:
            logger.warning("Neo4j backend unavailable (%s), falling back to SQLite", e)
            from src.services.graph_backend.sqlite_backend import SQLiteGraphBackend
            backend = SQLiteGraphBackend(db=db)
            return backend

    raise ValueError(
        f"Unknown graph_backend provider: {provider!r}. "
        f"Supported: sqlite, neo4j"
    )

"""图后端工厂 — 创建 SQLite 图后端实例"""
from __future__ import annotations

import logging

from src.services.graph_backend.base import GraphBackend

logger = logging.getLogger(__name__)


def create_graph_backend(config, db=None) -> GraphBackend:
    """创建图后端实例。

    外部图数据库后端已移除；旧配置中若仍存在非 sqlite
    provider，本函数会记录 warning 并回落到 SQLite，保证老配置可启动。
    """
    from src.services.db import Database
    from src.services.graph_backend.sqlite_backend import SQLiteGraphBackend

    db = db or Database
    provider = config.get("graph_backend.provider", "sqlite")
    if provider != "sqlite":
        logger.warning(
            "Graph backend provider %r is no longer supported; using SQLite",
            provider,
        )
    backend: GraphBackend = SQLiteGraphBackend(db=db)
    logger.info("Graph backend: SQLite")
    return backend

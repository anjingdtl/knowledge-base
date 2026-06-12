from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.deps import get_container
from src.api.routes.auth import _check_auth
from src.core.container import AppContainer

graph_router = APIRouter(prefix="/graph", tags=["graph"], dependencies=[Depends(_check_auth)])


class GraphTraverseReq(BaseModel):
    start_ids: list[str]
    start_type: str = "knowledge"
    max_depth: int = 2
    ref_types: list[str] | None = None


@graph_router.get("/unified")
def get_unified_graph(
    include_blocks: bool = True,
    include_tags: bool = True,
    block_limit: int | None = 1000,
    container: AppContainer = Depends(get_container),
):
    return container.unified_graph.build(
        include_blocks=include_blocks,
        include_tags=include_tags,
        block_limit=block_limit,
    )


@graph_router.post("/traverse")
def traverse_graph(data: GraphTraverseReq, container: AppContainer = Depends(get_container)):
    return container.graph_traversal.traverse(
        start_ids=data.start_ids,
        start_type=data.start_type,
        max_depth=data.max_depth,
        ref_types=data.ref_types,
    )


@graph_router.get("/backend/status")
def graph_backend_status(container: AppContainer = Depends(get_container)):
    """获取当前图后端状态"""
    backend = container.graph_backend
    return {
        "provider": backend.name,
        "healthy": backend.health_check(),
        "stats": backend.stats(),
    }


@graph_router.post("/backend/migrate")
def migrate_to_graph_backend(
    clear_target: bool = True,
    batch_size: int = 500,
    container: AppContainer = Depends(get_container),
):
    """将 SQLite 数据迁移到当前配置的图后端（如 Neo4j）"""
    backend = container.graph_backend
    if backend.name == "sqlite":
        return {"message": "当前后端已经是 SQLite，无需迁移"}

    from src.services.graph_backend.migration import GraphMigration
    migration = GraphMigration(config=container.config, db=container.db)
    result = migration.migrate_all(
        target=backend,
        clear_target=clear_target,
        batch_size=batch_size,
    )
    return result


@graph_router.post("/backend/sync")
def incremental_sync_graph_backend(
    since: str | None = None,
    container: AppContainer = Depends(get_container),
):
    """增量同步：将 since 时间之后变更的数据同步到图后端"""
    backend = container.graph_backend
    if backend.name == "sqlite":
        return {"message": "当前后端为 SQLite，无需增量同步"}

    from src.services.graph_backend.migration import GraphMigration
    migration = GraphMigration(config=container.config, db=container.db)
    result = migration.sync_incremental(target=backend, since=since)
    return result

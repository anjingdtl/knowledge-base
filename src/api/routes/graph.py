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


@graph_router.get("/visualize")
def visualize_graph(
    limit: int = 100,
    container: AppContainer = Depends(get_container),
):
    graph = container.unified_graph.build(
        include_blocks=False,
        include_tags=True,
        block_limit=limit,
    )
    return {
        "nodes": graph.get("nodes", [])[:limit],
        "edges": graph.get("edges", []),
    }


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
    """获取 SQLite 图后端状态"""
    backend = container.graph_backend
    return {
        "provider": backend.name,
        "healthy": backend.health_check(),
        "stats": backend.stats(),
    }

"""Stability-round2 测试公共 fixtures。

所有写测试均走根 conftest 的临时数据库；禁止触碰 data/kb.db。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.services.db import Database
from src.services.graph_backend.sqlite_backend import SQLiteGraphBackend


def _now() -> str:
    return datetime.now().isoformat()


def insert_knowledge(
    *,
    title: str,
    content: str = "content",
    tags: list[str] | None = None,
    file_type: str = "md",
    kid: str | None = None,
) -> str:
    item_id = kid or str(uuid.uuid4())
    Database.insert_knowledge({
        "id": item_id,
        "title": title,
        "content": content,
        "source_type": "manual",
        "source_path": "",
        "file_type": file_type,
        "file_size": 0,
        "content_hash": f"h-{item_id[:8]}",
        "file_created_at": "",
        "file_modified_at": "",
        "tags": json.dumps(tags or [], ensure_ascii=False),
        "version": 1,
        "created_at": _now(),
        "updated_at": _now(),
    })
    return item_id


def seed_chain_graph(n: int = 12) -> list[str]:
    """构造 n 个知识节点的链式引用图，保证 BFS 可遍历出足够节点。

    结构: k0 -> k1 -> ... -> k{n-1}（通过 entity_refs block references）
    每个知识页挂一个 block，block 引用下一页，形成 n 个 page 节点 + n 个 block 节点。
    """
    ids: list[str] = []
    for i in range(n):
        kid = insert_knowledge(title=f"Graph Node {i}", content=f"node-{i} body")
        ids.append(kid)
        Database.insert_blocks([{
            "id": f"b-{i}",
            "parent_id": None,
            "page_id": kid,
            "content": f"block for node {i}",
            "block_type": "text",
            "properties": "{}",
            "order_idx": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }])
    conn = Database.get_conn()
    for i in range(n - 1):
        conn.execute(
            "INSERT OR REPLACE INTO entity_refs "
            "(id, source_type, source_id, target_type, target_id, ref_type, weight) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"ref-{i}", "block", f"b-{i}", "knowledge", ids[i + 1], "references", 1.0),
        )
    conn.commit()
    return ids


def make_container():
    """最小容器：真实 DB + SQLite 图后端。"""
    return SimpleNamespace(
        db=Database,
        graph_backend=SQLiteGraphBackend(db=Database),
        llm=None,
    )


def node_id(node) -> str:
    if isinstance(node, dict):
        return str(node.get("source_id") or node.get("id") or "")
    return str(node)


def edge_source(edge: dict) -> str:
    return str(edge.get("source") or edge.get("from") or "")


def edge_target(edge: dict) -> str:
    return str(edge.get("target") or edge.get("to") or "")


def assert_no_dangling_edges(nodes: list, edges: list) -> None:
    ids = {node_id(n) for n in nodes}
    # 也接受带 page: / block: 前缀的 id
    expanded = set(ids)
    for nid in list(ids):
        if ":" in nid:
            expanded.add(nid.split(":", 1)[1])
        else:
            expanded.add(f"page:{nid}")
            expanded.add(f"block:{nid}")
    for e in edges:
        src, tgt = edge_source(e), edge_target(e)
        assert src in expanded, f"dangling edge source={src!r} not in nodes"
        assert tgt in expanded, f"dangling edge target={tgt!r} not in nodes"


def assert_paths_in_nodes(nodes: list, paths: list) -> None:
    ids = {node_id(n) for n in nodes}
    expanded = set(ids)
    for nid in list(ids):
        if ":" in nid:
            expanded.add(nid.split(":", 1)[1])
        else:
            expanded.add(f"page:{nid}")
            expanded.add(f"block:{nid}")
    for path in paths:
        for nid in path:
            assert str(nid) in expanded, f"path node {nid!r} not in page nodes"


@pytest.fixture
def graph_ids():
    return seed_chain_graph(12)


@pytest.fixture
def patch_container(monkeypatch):
    container = make_container()
    monkeypatch.setattr("src.mcp.tools.graph._get_container", lambda: container)
    monkeypatch.setattr("src.mcp.tools.retrieval._get_container", lambda: container)
    monkeypatch.setattr("src.mcp.tools.ingest._get_container", lambda: container)
    monkeypatch.setattr("src.mcp.tools.memory._get_container", lambda: container)
    return container

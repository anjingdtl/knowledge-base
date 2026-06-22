"""Build source graph payloads for RAG answers from local graph tables."""
from __future__ import annotations

from src.services.db import Database


def build_source_graph(
    sources: list[dict] | None,
    db=None,
    max_nodes: int | None = None,
    graph_backend=None,
) -> dict:
    """构造 RAG 答案的 source_graph payload。

    Args:
        sources: 来源列表，每条含 ``block_id`` / ``knowledge_id`` 字段
        db: 数据库实例（默认使用 Database 单例）
        max_nodes: 节点数上限；超过则截断并设 ``truncated=True``。
                   缺省时从 config ``rag.max_graph_nodes`` 读取，再缺省 200。
        graph_backend: 图后端实例（可选）；为 None 时使用 SQLite 后端。

    Returns:
        ``{"nodes": [...], "edges": [...], "truncated": bool, "node_count": int}``
    """
    if max_nodes is None:
        from src.utils.config import Config
        max_nodes = int(Config.get("rag.max_graph_nodes", 200))
    db = db or Database

    # 如果提供了图后端，使用后端优化的构建路径
    normalized_sources = sources or []
    if graph_backend is not None and graph_backend.name != "sqlite":
        return _build_source_graph_via_backend(
            normalized_sources, db, max_nodes, graph_backend,
        )

    # 默认路径：直接从 SQLite 构建（与改造前行为一致）
    return _build_source_graph_sqlite(normalized_sources, db, max_nodes)


def _build_source_graph_via_backend(
    sources: list[dict],
    db,
    max_nodes: int,
    backend,
) -> dict:
    """通过自定义图后端构建 source graph。"""
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}
    truncated = False

    def add_node(node_id: str, node_type: str, label: str, **extra):
        nonlocal truncated
        if not node_id:
            return
        if node_id in nodes:
            return
        if len(nodes) >= max_nodes:
            truncated = True
            return
        nodes[node_id] = {"id": node_id, "type": node_type, "label": label, **extra}

    def add_edge(source: str, target: str, edge_type: str):
        if source and target:
            edges.setdefault((source, target, edge_type), {
                "source": source,
                "target": target,
                "type": edge_type,
            })

    sources = sources or []

    # 收集所有起始节点 ID
    start_ids = []
    for source in sources:
        metadata = source.get("metadata") or {}
        bid = source.get("block_id") or source.get("chunk_id") or source.get("id") or metadata.get("block_id")
        kid = source.get("knowledge_id") or metadata.get("knowledge_id") or metadata.get("page_id")
        if kid:
            start_ids.append(f"page:{kid}")
        if bid:
            start_ids.append(f"block:{bid}")

    if not start_ids:
        return {"nodes": [], "edges": [], "truncated": False, "node_count": 0}

    # 使用后端遍历获取子图（depth=2 覆盖祖先链和引用关系）
    result = backend.traverse(
        start_ids=start_ids,
        max_depth=2,
        max_nodes=max_nodes,
    )

    # 转换为 source graph 格式
    for node_data in result.nodes:
        node_type = node_data.get("type", "page")
        node_id = node_data.get("id", "")
        label = node_data.get("label", "")
        props = node_data.get("properties", {})

        if node_type in ("page", "knowledge"):
            add_node(node_id, "knowledge", label)
        elif node_type == "block":
            add_node(node_id, "block", label, knowledge_id=props.get("page_id", ""))
        else:
            add_node(node_id, node_type, label)

    for edge_data in result.edges:
        add_edge(
            edge_data.get("source", ""),
            edge_data.get("target", ""),
            edge_data.get("type", "link"),
        )

    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "truncated": truncated,
        "node_count": len(nodes),
    }


def _build_source_graph_sqlite(sources, db, max_nodes: int) -> dict:
    """直接从 SQLite 构建 source graph（与改造前行为一致）"""
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}
    truncated = False

    def add_node(node_id: str, node_type: str, label: str, **extra):
        nonlocal truncated
        if not node_id:
            return
        if node_id in nodes:
            return
        if len(nodes) >= max_nodes:
            truncated = True
            return
        nodes[node_id] = {"id": node_id, "type": node_type, "label": label, **extra}

    def add_edge(source: str, target: str, edge_type: str):
        if source and target:
            edges.setdefault((source, target, edge_type), {
                "source": source,
                "target": target,
                "type": edge_type,
            })

    sources = sources or []

    knowledge_ids = set()
    block_ids = set()
    for source in sources:
        metadata = source.get("metadata") or {}
        bid = source.get("block_id") or source.get("chunk_id") or source.get("id") or metadata.get("block_id")
        kid = source.get("knowledge_id") or metadata.get("knowledge_id") or metadata.get("page_id")
        if kid:
            knowledge_ids.add(kid)
        if bid:
            block_ids.add(bid)

    knowledge_cache = {}
    if knowledge_ids:
        batch = db.get_knowledge_batch(list(knowledge_ids))
        knowledge_cache.update(batch)

    block_cache = {}
    if block_ids:
        conn = db.get_conn()
        placeholders = ",".join("?" for _ in block_ids)
        rows = conn.execute(
            f"SELECT id, page_id, content FROM blocks WHERE id IN ({placeholders})",
            list(block_ids),
        ).fetchall()
        for r in rows:
            block_cache[r["id"]] = {"id": r["id"], "page_id": r["page_id"], "content": r["content"]}

    # Explicit block_id inputs may not carry knowledge_id. Once blocks are loaded,
    # fetch their owning pages so the graph can still expose page -> block provenance.
    page_ids_from_blocks = {
        block.get("page_id") for block in block_cache.values() if block.get("page_id")
    }
    missing_page_ids = page_ids_from_blocks - set(knowledge_cache.keys())
    if missing_page_ids:
        batch = db.get_knowledge_batch(list(missing_page_ids))
        knowledge_cache.update(batch)

    # 一次性批量回溯所有 block 的祖先链 — 替代之前两次循环里反复调用
    # get_block_ancestors 触发的 N+1 查询（曾经是 knowledge 量大时主要的
    # 卡顿源头之一）。
    ancestors_by_block: dict[str, list[dict]] = {}
    if block_ids and hasattr(db, "get_block_ancestors_batch"):
        ancestors_by_block = db.get_block_ancestors_batch(list(block_ids), max_depth=10)

    ancestor_ids = set()
    for ancestors in ancestors_by_block.values():
        for ancestor in ancestors:
            ancestor_ids.add(ancestor["id"])
            block_cache.setdefault(ancestor["id"], {
                "id": ancestor["id"],
                "page_id": ancestor.get("page_id"),
                "content": ancestor.get("content"),
            })

    all_block_ids_for_refs = block_ids | ancestor_ids
    ref_map: dict[str, list[dict]] = {bid: [] for bid in all_block_ids_for_refs}
    if all_block_ids_for_refs:
        conn = db.get_conn()
        placeholders = ",".join("?" for _ in all_block_ids_for_refs)
        ref_rows = conn.execute(
            f"""SELECT source_id, target_type, target_id, ref_type FROM entity_refs
                WHERE source_type = 'block' AND source_id IN ({placeholders})""",
            list(all_block_ids_for_refs),
        ).fetchall()
        for r in ref_rows:
            ref_map.setdefault(r["source_id"], []).append({
                "target_type": r["target_type"],
                "target_id": r["target_id"],
                "ref_type": r["ref_type"],
            })

    ref_target_kids = set()
    ref_target_bids = set()
    for refs in ref_map.values():
        for ref in refs:
            if ref["target_type"] == "knowledge":
                ref_target_kids.add(ref["target_id"])
            elif ref["target_type"] == "block":
                ref_target_bids.add(ref["target_id"])
    if ref_target_kids - knowledge_ids:
        batch = db.get_knowledge_batch(list(ref_target_kids - knowledge_ids))
        knowledge_cache.update(batch)
    if ref_target_bids - block_cache.keys():
        conn = db.get_conn()
        placeholders = ",".join("?" for _ in (ref_target_bids - block_cache.keys()))
        rows = conn.execute(
            f"SELECT id, page_id, content FROM blocks WHERE id IN ({placeholders})",
            list(ref_target_bids - block_cache.keys()),
        ).fetchall()
        for r in rows:
            block_cache[r["id"]] = {"id": r["id"], "page_id": r["page_id"], "content": r["content"]}

    for source in sources:
        metadata = source.get("metadata") or {}
        block_id = (
            source.get("block_id")
            or source.get("chunk_id")
            or source.get("id")
            or metadata.get("block_id")
        )
        knowledge_id = source.get("knowledge_id") or metadata.get("knowledge_id") or metadata.get("page_id")

        if knowledge_id:
            item = knowledge_cache.get(knowledge_id)
            if item:
                add_node(knowledge_id, "knowledge", item.get("title", knowledge_id))

        if block_id:
            block = block_cache.get(block_id)
            if block:
                label = (block.get("content") or block_id).replace("\n", " ")[:80]
                add_node(block_id, "block", label, knowledge_id=block.get("page_id"))
                if block.get("page_id"):
                    item = knowledge_cache.get(block["page_id"])
                    if item:
                        add_node(block["page_id"], "knowledge", item.get("title", block["page_id"]))
                        add_edge(block["page_id"], block_id, "contains")
                # 直接复用前面批量查到的祖先链 — 避免再次触发 N+1。
                current_id = block_id
                for ancestor in ancestors_by_block.get(block_id, []):
                    a_block = block_cache.get(ancestor["id"])
                    a_content = (a_block.get("content") if a_block else ancestor.get("content")) or ancestor["id"]
                    add_node(
                        ancestor["id"],
                        "block",
                        a_content.replace("\n", " ")[:80],
                        knowledge_id=ancestor.get("page_id"),
                    )
                    add_edge(ancestor["id"], current_id, "parent")
                    current_id = ancestor["id"]

        if block_id:
            for ref in ref_map.get(block_id, []):
                target_type = ref["target_type"]
                target_id = ref["target_id"]
                if target_type == "knowledge":
                    item = knowledge_cache.get(target_id)
                    add_node(target_id, "knowledge", item.get("title", target_id) if item else target_id)
                elif target_type == "block":
                    target = block_cache.get(target_id)
                    if target:
                        add_node(
                            target_id,
                            "block",
                            (target.get("content") or target_id)[:80],
                            knowledge_id=target.get("page_id"),
                        )
                add_edge(block_id, target_id, ref["ref_type"] or "link")

    # 补充：从 knowledge_graph_relations 加载 LLM 发现的语义关系边
    if knowledge_ids:
        try:
            placeholders = ",".join("?" for _ in knowledge_ids)
            kg_rows = conn.execute(
                f"""SELECT source_knowledge_id, target_knowledge_id, relation_type
                    FROM knowledge_graph_relations
                    WHERE source_knowledge_id IN ({placeholders})
                       OR target_knowledge_id IN ({placeholders})""",
                list(knowledge_ids) + list(knowledge_ids),
            ).fetchall()
            for r in kg_rows:
                src_kid = r["source_knowledge_id"]
                tgt_kid = r["target_knowledge_id"]
                rel_type = r["relation_type"] or "related"
                # 确保相关知识节点也在图中
                if src_kid not in knowledge_cache:
                    batch = db.get_knowledge_batch([src_kid])
                    knowledge_cache.update(batch)
                if tgt_kid not in knowledge_cache:
                    batch = db.get_knowledge_batch([tgt_kid])
                    knowledge_cache.update(batch)
                src_item = knowledge_cache.get(src_kid)
                tgt_item = knowledge_cache.get(tgt_kid)
                if src_item:
                    add_node(src_kid, "knowledge", src_item.get("title", src_kid))
                if tgt_item:
                    add_node(tgt_kid, "knowledge", tgt_item.get("title", tgt_kid))
                add_edge(src_kid, tgt_kid, rel_type)
        except Exception:
            pass  # knowledge_graph_relations 表可能不存在

    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "truncated": truncated,
        "node_count": len(nodes),
    }

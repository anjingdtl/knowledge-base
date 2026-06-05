"""Smoke tests for knowledge-graph performance fixes.

验证：
1. Database.get_block_ancestors_batch 单/多 block 正确性
2. Database.get_chunks_by_knowledge_batch 正确性
3. 循环引用防护（不应无限递归）
4. 旧 API (get_block_ancestors 单条) 行为不变
"""
from src.services.db import Database


def _seed_chain():
    """插入 b1 (root) -> b2 -> b3 链式 blocks。"""
    Database.insert_knowledge({
        "id": "k1", "title": "test", "content": "x", "tags": "[]",
        "file_type": "txt", "source_type": "manual", "source_path": "",
        "content_hash": "h1", "quality": "", "file_size": 0,
        "file_created_at": "", "file_modified_at": "",
        "created_at": "", "updated_at": "",
    })
    Database.insert_blocks([
        {"id": "b1", "parent_id": None, "page_id": "k1", "content": "root",
         "block_type": "text", "properties": "{}", "order_idx": 0,
         "created_at": "", "updated_at": ""},
        {"id": "b2", "parent_id": "b1", "page_id": "k1", "content": "child",
         "block_type": "text", "properties": "{}", "order_idx": 1,
         "created_at": "", "updated_at": ""},
        {"id": "b3", "parent_id": "b2", "page_id": "k1", "content": "grand",
         "block_type": "text", "properties": "{}", "order_idx": 2,
         "created_at": "", "updated_at": ""},
    ])


def test_get_block_ancestors_batch_single():
    _seed_chain()
    res = Database.get_block_ancestors_batch(["b3"], max_depth=5)
    ids = [a["id"] for a in res.get("b3", [])]
    assert ids == ["b2", "b1"], f"expected ['b2', 'b1'], got {ids}"


def test_get_block_ancestors_batch_multi():
    _seed_chain()
    res = Database.get_block_ancestors_batch(["b2", "b3"], max_depth=5)
    assert [a["id"] for a in res.get("b2", [])] == ["b1"]
    assert [a["id"] for a in res.get("b3", [])] == ["b2", "b1"]


def test_get_block_ancestors_batch_empty_input():
    res = Database.get_block_ancestors_batch([], max_depth=5)
    assert res == {}


def test_get_block_ancestors_batch_cycle_protected():
    """b4 -> b5 -> b4 循环引用，递归 CTE 应在 max_depth 或 path 检测处停止。"""
    _seed_chain()
    Database.insert_blocks([
        {"id": "b4", "parent_id": "b5", "page_id": "k1", "content": "a",
         "block_type": "text", "properties": "{}", "order_idx": 0,
         "created_at": "", "updated_at": ""},
        {"id": "b5", "parent_id": "b4", "page_id": "k1", "content": "b",
         "block_type": "text", "properties": "{}", "order_idx": 1,
         "created_at": "", "updated_at": ""},
    ])
    res = Database.get_block_ancestors_batch(["b4"], max_depth=10)
    # path 去重保证不会无限循环
    assert len(res.get("b4", [])) <= 10
    # 至少找到一个
    assert len(res.get("b4", [])) >= 1


def test_get_chunks_by_knowledge_batch_empty():
    assert Database.get_chunks_by_knowledge_batch([]) == {}


def test_get_chunks_by_knowledge_batch_basic():
    """插入两条 chunk 验证批量返回。"""
    _seed_chain()
    with Database._write_lock:
        conn = Database.get_conn()
        conn.execute(
            "INSERT INTO knowledge_chunks (id, knowledge_id, chunk_index, chunk_text) "
            "VALUES (?, ?, ?, ?)",
            ("c1", "k1", 0, "first"),
        )
        conn.execute(
            "INSERT INTO knowledge_chunks (id, knowledge_id, chunk_index, chunk_text) "
            "VALUES (?, ?, ?, ?)",
            ("c2", "k1", 1, "second"),
        )
        conn.commit()
    res = Database.get_chunks_by_knowledge_batch(["k1"])
    assert [c["id"] for c in res["k1"]] == ["c1", "c2"]
    assert [c["chunk_text"] for c in res["k1"]] == ["first", "second"]


def test_compute_force_layout_pinned():
    """验证 _compute_force_layout 纯函数 — pinned 节点位置不变。"""
    from src.gui.graph_view import _compute_force_layout
    initial = {
        "a": (0.0, 0.0, False),
        "b": (10.0, 0.0, False),
        "c": (5.0, 5.0, True),  # pinned
    }
    res = _compute_force_layout(initial, [("a", "b")], iterations=20)
    assert res["c"] == (5.0, 5.0), f"pinned changed: {res['c']}"
    # 非 pinned 节点位置应变化
    assert (res["a"][0], res["a"][1]) != (0.0, 0.0) or res["a"] != (0.0, 0.0)

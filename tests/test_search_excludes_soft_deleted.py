"""第6轮 BUG#13 回归测试：软删除条目的 blocks/vectors 不应泄漏到搜索结果。

复现路径：
- BlockStore.search（vec_blocks 向量搜索）
- Database.search_blocks_fts（block_fts 全文搜索）
- Database.search_chunks_fts（chunk_fts 全文搜索）
- KnowledgeRepository.search_chunks_fts（同上，repo 版）

软删（只置 knowledge_items.deleted_at）后，以上搜索不应再返回该条目的 block/chunk。
"""
from datetime import datetime

from tests.conftest import insert_test_block, insert_test_knowledge


def _now():
    return datetime.now().isoformat()


def _soft_delete(kid):
    """软删一条知识（只置 deleted_at，不动 blocks/vectors/fts）"""
    from src.services.db import Database
    conn = Database.get_conn()
    conn.execute(
        "UPDATE knowledge_items SET deleted_at = ? WHERE id = ?",
        (_now(), kid),
    )
    conn.commit()


def test_block_vector_search_excludes_soft_deleted():
    """BUG#13：BlockStore.search 向量路径过滤软删条目"""
    from src.services.block_store import BlockStore

    kid = insert_test_knowledge(
        title="软删向量测试",
        content="向量搜索过滤软删条目",
        tags=["bug13"],
    )
    bid = insert_test_block(kid, content="向量搜索过滤软删条目", block_type="text")

    store = BlockStore()
    store.add_block_embedding(bid, [0.1] * 1024)

    # 软删前命中
    results = store.search("向量搜索", top_k=10, query_embedding=[0.1] * 1024)
    assert any(r["id"] == bid for r in results), "软删前应命中该 block"

    _soft_delete(kid)

    # 软删后不应返回该 block
    results = store.search("向量搜索", top_k=10, query_embedding=[0.1] * 1024)
    leaked = [r for r in results if r.get("metadata", {}).get("page_id") == kid]
    assert not leaked, f"软删后 block 仍泄漏: {leaked}"


def test_block_fts_search_excludes_soft_deleted():
    """BUG#13：Database.search_blocks_fts 过滤软删条目"""
    from src.services.db import Database

    kid = insert_test_knowledge(
        title="软删FTS测试",
        content="全文搜索过滤软删条目",
        tags=["bug13"],
    )
    bid = insert_test_block(kid, content="全文搜索过滤软删条目", block_type="text")

    blocks = [
        {
            "id": bid,
            "parent_id": None,
            "page_id": kid,
            "content": "全文搜索过滤软删条目",
            "block_type": "text",
            "properties": "{}",
            "order_idx": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }
    ]
    Database.insert_blocks_fts(blocks)

    # 软删前命中
    results = Database.search_blocks_fts("全文搜索", limit=10)
    assert any(r["id"] == bid for r in results), "软删前应命中该 block"

    _soft_delete(kid)

    # 软删后不应返回
    results = Database.search_blocks_fts("全文搜索", limit=10)
    leaked = [r for r in results if r["page_id"] == kid]
    assert not leaked, f"软删后 block_fts 仍泄漏: {leaked}"


def test_chunk_fts_search_excludes_soft_deleted():
    """BUG#13：Database.search_chunks_fts 过滤软删条目"""
    from src.services.db import Database
    from src.utils.chinese_tokenizer import tokenize_chinese_full

    kid = insert_test_knowledge(
        title="软删Chunk测试",
        content="分块搜索过滤软删条目",
        tags=["bug13"],
    )
    chunk_id = "chunk-bug13"
    text = "分块搜索过滤软删条目"
    segmented = tokenize_chinese_full(text)
    conn = Database.get_conn()
    conn.execute(
        """INSERT INTO knowledge_chunks (id, knowledge_id, chunk_index, chunk_text, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (chunk_id, kid, 0, text, _now()),
    )
    conn.execute(
        """INSERT INTO chunk_fts (fts_segmented, knowledge_id, chunk_id)
           VALUES (?, ?, ?)""",
        (segmented, kid, chunk_id),
    )
    conn.commit()

    # 软删前命中
    results = Database.search_chunks_fts("分块搜索", limit=10)
    assert any(r["id"] == chunk_id for r in results), "软删前应命中该 chunk"

    _soft_delete(kid)

    # 软删后不应返回
    results = Database.search_chunks_fts("分块搜索", limit=10)
    leaked = [r for r in results if r.get("knowledge_id") == kid]
    assert not leaked, f"软删后 chunk_fts 仍泄漏: {leaked}"


def test_repo_chunk_fts_search_excludes_soft_deleted():
    """BUG#13：KnowledgeRepository.search_chunks_fts 过滤软删条目"""
    from src.repositories.knowledge_repo import KnowledgeRepository
    from src.services.db import Database
    from src.utils.chinese_tokenizer import tokenize_chinese_full

    kid = insert_test_knowledge(
        title="软删RepoChunk测试",
        content="仓库分块搜索过滤软删条目",
        tags=["bug13"],
    )
    chunk_id = "chunk-repo-bug13"
    text = "仓库分块搜索过滤软删条目"
    segmented = tokenize_chinese_full(text)
    conn = Database.get_conn()
    conn.execute(
        """INSERT INTO knowledge_chunks (id, knowledge_id, chunk_index, chunk_text, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (chunk_id, kid, 0, text, _now()),
    )
    conn.execute(
        """INSERT INTO chunk_fts (fts_segmented, knowledge_id, chunk_id)
           VALUES (?, ?, ?)""",
        (segmented, kid, chunk_id),
    )
    conn.commit()

    repo = KnowledgeRepository()
    # 软删前命中
    results = repo.search_chunks_fts("仓库分块", limit=10)
    assert any(r["id"] == chunk_id for r in results), "软删前应命中该 chunk"

    _soft_delete(kid)

    # 软删后不应返回
    results = repo.search_chunks_fts("仓库分块", limit=10)
    leaked = [r for r in results if r.get("knowledge_id") == kid]
    assert not leaked, f"软删后 repo chunk_fts 仍泄漏: {leaked}"

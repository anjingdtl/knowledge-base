"""v1.6.0 全量 MCP 稳定性测试报告 — 问题修复回归

覆盖：
P0 向量覆盖率误统计（含软删 blocks）
P0 reindex_checkpoint 僵尸（started_at=NULL / processing）
P0 reindex 续传跳过未向量化条目
P0 reindex 时 content_hash 自命中导致跳过重建
P1 reclaim / worker 启动清理
P2 缓存命中率冷启动不应误报异常
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.models.knowledge import KnowledgeItem
from src.services.db import Database
from src.services.indexer import (
    index_knowledge_item,
    reindex_all,
    reindex_knowledge_item,
    _clear_reindex_checkpoint,
    _save_reindex_checkpoint,
)


# ---------------------------------------------------------------------------
# P0: 健康检查向量覆盖率排除软删
# ---------------------------------------------------------------------------

class TestHealthVectorCoverageExcludesSoftDeleted:
    def test_soft_deleted_blocks_not_counted_in_coverage(self, setup_db, monkeypatch):
        """软删条目的 blocks 不得压低 vector_coverage。"""
        from src.services.health import kb_health_check
        from src.services.block_store import BlockStore

        # 活跃条目：1 block + 1 vector
        active = KnowledgeItem(
            title="active-doc",
            content="活跃文档内容用于覆盖率测试" * 5,
            source_type="manual",
            file_type="txt",
        )
        Database.insert_knowledge(active.to_row())
        now = datetime.now().isoformat()
        active_block = {
            "id": "blk-active-1",
            "parent_id": None,
            "page_id": active.id,
            "content": active.content,
            "block_type": "text",
            "properties": "{}",
            "order_idx": 0,
            "created_at": now,
            "updated_at": now,
        }
        Database.insert_blocks([active_block])
        BlockStore().add_block_embedding("blk-active-1", [0.1] * 1024)

        # 软删条目：大量 blocks 无向量 — 旧逻辑会把覆盖率拉到接近 0
        deleted = KnowledgeItem(
            title="deleted-doc",
            content="软删文档" * 20,
            source_type="manual",
            file_type="txt",
        )
        Database.insert_knowledge(deleted.to_row())
        del_blocks = [
            {
                "id": f"blk-del-{i}",
                "parent_id": None,
                "page_id": deleted.id,
                "content": f"soft deleted block {i}",
                "block_type": "text",
                "properties": "{}",
                "order_idx": i,
                "created_at": now,
                "updated_at": now,
            }
            for i in range(9)
        ]
        Database.insert_blocks(del_blocks)
        Database.soft_delete_knowledge(deleted.id)

        report = kb_health_check()
        # 活跃 1/1 = 100%；若计入软删 1/10 = 10%
        assert report["total_blocks"] == 1, (
            f"total_blocks 应仅计活跃，实际 {report['total_blocks']}"
        )
        assert report["vector_coverage"] >= 0.99, (
            f"vector_coverage 应接近 1.0，实际 {report['vector_coverage']}"
        )


# ---------------------------------------------------------------------------
# P0/P1: reclaim_stuck_jobs 覆盖 started_at=NULL + reindex_checkpoint
# ---------------------------------------------------------------------------

class TestReclaimNullStartedAtAndCheckpoint:
    def test_reclaim_null_started_at_running_job(self, setup_db):
        """started_at=NULL 的 running 任务也应被回收（旧 reindex 僵尸形态）。"""
        from src.services.async_task import AsyncTaskService

        job_id = AsyncTaskService.create_job("file_ingest", {"file_path": "/tmp/x.txt"})
        conn = Database.get_conn()
        old_created = (datetime.now() - timedelta(hours=48)).isoformat()
        conn.execute(
            "UPDATE async_jobs SET status = 'running', started_at = NULL, created_at = ? WHERE id = ?",
            (old_created, job_id),
        )
        conn.commit()

        reclaimed = Database.reclaim_stuck_jobs(timeout_hours=6)
        assert reclaimed >= 1
        row = conn.execute(
            "SELECT status FROM async_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert row["status"] == "pending"

    def test_reclaim_deletes_stale_reindex_checkpoint(self, setup_db):
        """reindex_checkpoint 僵尸应被删除，不能变成 pending 被 worker 认领。"""
        conn = Database.get_conn()
        old_created = (datetime.now() - timedelta(days=19)).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO async_jobs "
            "(id, job_type, status, params, created_at, started_at) "
            "VALUES ('reindex_checkpoint', 'reindex_all', 'processing', ?, ?, NULL)",
            (json.dumps({"processed_ids": ["a", "b"]}), old_created),
        )
        conn.commit()

        Database.reclaim_stuck_jobs(timeout_hours=6)

        row = conn.execute(
            "SELECT id FROM async_jobs WHERE id = 'reindex_checkpoint'"
        ).fetchone()
        assert row is None, "僵尸 reindex_checkpoint 应被删除而非回退为 pending"


# ---------------------------------------------------------------------------
# P0: reindex 续传不应跳过未完整向量化的条目
# ---------------------------------------------------------------------------

class TestReindexResumeSkipsOnlyFullyVectorized:
    def test_incomplete_vector_items_not_skipped(self, setup_db, monkeypatch):
        """checkpoint 里的 processed_ids 若仍缺向量，restart 续传不得跳过。"""
        from src.services.block_store import BlockStore

        mock_embeddings = [[0.2] * 1024 for _ in range(50)]

        class MockEmbeddingService:
            def build_embedding_text(self, block):
                if isinstance(block, dict):
                    return block.get("content") or ""
                return str(block or "")

            def embed_batch_with_cache(self, texts, batch_size=20):
                return mock_embeddings[: len(texts)]

        monkeypatch.setattr(
            "src.services.embedding.EmbeddingService",
            MockEmbeddingService,
        )

        # 确保 vec_blocks 表存在（reindex 孤儿清理会查该表）
        BlockStore()._ensure_table()

        item = KnowledgeItem(
            title="缺向量条目",
            content="需要被 reindex 的内容" * 30,
            source_type="manual",
            file_type="txt",
        )
        Database.insert_knowledge(item.to_row())
        # 只写 blocks，不写向量 → 覆盖率缺口
        now = datetime.now().isoformat()
        Database.insert_blocks([{
            "id": "blk-no-vec",
            "parent_id": None,
            "page_id": item.id,
            "content": item.content,
            "block_type": "text",
            "properties": "{}",
            "order_idx": 0,
            "created_at": now,
            "updated_at": now,
        }])

        # 近期 checkpoint（未过期）声称已处理该 item，专门验证「缺向量不跳过」
        # 而非「过期 checkpoint 被清理」路径
        conn = Database.get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO async_jobs "
            "(id, job_type, status, params, created_at, started_at) "
            "VALUES ('reindex_checkpoint', 'reindex_all', 'running', ?, ?, ?)",
            (
                json.dumps({"processed_ids": [item.id]}),
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()

        result = reindex_all(dry_run=False, restart=True)
        # 旧逻辑会 skip=1 success=0；修复后应 success>=1
        assert result["success"] >= 1, f"缺向量条目应被 reindex，result={result}"
        assert result["skipped"] == 0


# ---------------------------------------------------------------------------
# P0: reindex 绕过 content_hash 自命中
# ---------------------------------------------------------------------------

class TestReindexBypassesContentHashDedup:
    def test_reindex_knowledge_item_not_skipped_by_own_hash(self, setup_db, monkeypatch):
        """reindex 删除旧 blocks 后，不得因 content_hash 命中自身而跳过重建。"""
        import hashlib

        mock_embeddings = [[0.3] * 1024 for _ in range(20)]
        calls = {"n": 0}

        class MockEmbeddingService:
            def build_embedding_text(self, block):
                if isinstance(block, dict):
                    return block.get("content") or ""
                return str(block or "")

            def embed_batch_with_cache(self, texts, batch_size=20):
                calls["n"] += 1
                return mock_embeddings[: len(texts)]

        monkeypatch.setattr(
            "src.services.embedding.EmbeddingService",
            MockEmbeddingService,
        )

        content = "用于验证 reindex 不被自身 hash 跳过" * 20
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        item = KnowledgeItem(
            title="hash-self-dedup",
            content=content,
            source_type="manual",
            file_type="txt",
            content_hash=content_hash,
        )
        Database.insert_knowledge(item.to_row())
        # 强制写 hash（确保 DB 可被 get_knowledge_by_hash 命中自身）
        Database.get_conn().execute(
            "UPDATE knowledge_items SET content_hash = ? WHERE id = ?",
            (content_hash, item.id),
        )
        Database.get_conn().commit()
        index_knowledge_item(item, skip_dedup=True)

        conn = Database.get_conn()
        before = conn.execute(
            "SELECT COUNT(*) FROM blocks WHERE page_id = ?", (item.id,)
        ).fetchone()[0]
        assert before >= 1

        # 重新取 item 行构造 KnowledgeItem（含 content_hash）
        row = conn.execute(
            "SELECT * FROM knowledge_items WHERE id = ?", (item.id,)
        ).fetchone()
        item2 = KnowledgeItem(
            id=row["id"],
            title=row["title"],
            content=row["content"],
            source_type=row["source_type"],
            source_path=row["source_path"] or "",
            file_type=row["file_type"] or "txt",
            content_hash=row["content_hash"] or "",
        )
        calls["n"] = 0
        reindex_knowledge_item(item.id, item2)

        after = conn.execute(
            "SELECT COUNT(*) FROM blocks WHERE page_id = ?", (item.id,)
        ).fetchone()[0]
        assert after >= 1, "reindex 后 blocks 不应被清空（hash 自命中跳过）"
        assert calls["n"] >= 1, "reindex 应再次调用 embedding"


# ---------------------------------------------------------------------------
# P2: 冷启动缓存命中率
# ---------------------------------------------------------------------------

class TestCacheHitRateColdStart:
    def test_zero_samples_not_reported_as_zero_hit_rate_anomaly(self, setup_db):
        """无 embedding 采样时 hit_rate 应为 None（或 samples=0），不应被当成 0% 异常。"""
        from src.services.embedding import _l1_cache
        from src.services.health import kb_health_check

        _l1_cache.clear()
        report = kb_health_check()
        cache = report["cache_hit_rate"]
        # 修复后：无样本时 embedding 为 None，并带 samples 字段
        assert cache.get("embedding") is None or cache.get("embedding_samples", 0) == 0
        assert "embedding_samples" in cache
        # 冷启动 0 样本不应写入 warnings
        assert not any("缓存" in w and "0%" in w for w in report.get("warnings", []))

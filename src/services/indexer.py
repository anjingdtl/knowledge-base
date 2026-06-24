"""知识条目索引 — Block-First 管线（分块 + 向量化 + 全文索引）"""
import json
import logging
import uuid
from datetime import datetime
from typing import Callable

from src.models.knowledge import KnowledgeChunk, KnowledgeItem
from src.services.block_store import BlockStore
from src.services.db import Database
from src.services.text_splitter import TextChunk, split_code, split_markdown, split_text
from src.services.vectorstore import VectorStore
from src.utils.config import Config

logger = logging.getLogger(__name__)


def _check_content_hash(content: str) -> tuple[str | None, str]:
    """检查内容hash是否已存在，返回 (已有文档ID或None, 计算出的hash)"""
    if not content:
        return None, ""
    import hashlib
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = Database.get_knowledge_by_hash(content_hash)
    if existing:
        logger.info(f"Content duplicate detected: existing_id={existing['id']}, title={existing.get('title', '')}")
        return existing["id"], content_hash
    return None, content_hash


def _validate_content_quality(blocks: list[dict]) -> tuple[int, list[str]]:
    """评估内容质量，返回 (quality_score, warnings)

    quality_score: 0-100
      - block_count_score (30%): 0→0, 1-3→40, 4-10→70, 11+→100
      - text_density_score (40%): 非标题block占比
      - content_length_score (30%): 总内容长度
    """
    warnings = []
    if not blocks:
        warnings.append("empty_content")
        return 0, warnings

    total_blocks = len(blocks)
    total_content = sum(len(b.get("content", "")) for b in blocks)

    # Block count score
    if total_blocks <= 3:
        block_count_score = 40
    elif total_blocks <= 10:
        block_count_score = 70
    else:
        block_count_score = 100

    # Text density score — 非纯标题block占比
    substantive_blocks = sum(
        1 for b in blocks
        if len(b.get("content", "").strip()) > 30  # 超过30字算有实质内容
    )
    text_density_score = int((substantive_blocks / max(total_blocks, 1)) * 100)

    # Content length score
    if total_content == 0:
        content_length_score = 0
    elif total_content < 100:
        content_length_score = 30
    elif total_content < 500:
        content_length_score = 60
    else:
        content_length_score = 100

    quality_score = int(
        block_count_score * 0.3
        + text_density_score * 0.4
        + content_length_score * 0.3
    )

    if quality_score == 0:
        warnings.append("empty_content")
    elif quality_score < 30:
        warnings.append("low_quality")

    return quality_score, warnings


def index_knowledge_item(item: KnowledgeItem):
    """将知识条目分块并存入 DB + 向量库 + 全文索引（Block-First 管线）"""
    # 统一去重拦截入口
    existing_id, computed_hash = _check_content_hash(item.content)
    if existing_id:
        logger.info(f"Skipping duplicate content for {item.id}: already exists as {existing_id}")
        return existing_id
    # 将计算出的hash存入item，确保后续insert_knowledge写入DB
    if computed_hash and not item.content_hash:
        item.content_hash = computed_hash
    tags_str = ",".join(item.tags)
    chunk_size = Config.get("rag.chunk_size", 500)
    chunk_overlap = Config.get("rag.chunk_overlap", 50)
    base_meta = {
        "knowledge_id": item.id,
        "tags": tags_str,
        "title": item.title,
        "source_path": item.source_path,
        "source_type": item.source_type,
        "file_type": item.file_type,
        "created_at": item.created_at,
    }

    if item.file_type == "md":
        chunks = split_markdown(item.content, chunk_size=chunk_size,
                                chunk_overlap=chunk_overlap, metadata=base_meta)
    elif item.file_type == "code":
        chunks = split_code(item.content, chunk_size=chunk_size,
                            chunk_overlap=chunk_overlap, metadata=base_meta)
    else:
        chunks = split_text(item.content, chunk_size=chunk_size,
                            chunk_overlap=chunk_overlap, metadata=base_meta)
        # 非 Markdown 文件：在分块文本前注入标题，确保 FTS 能匹配到标题关键词
        if item.title and chunks:
            title_prefix = f"[标题: {item.title}]\n"
            chunks = [
                TextChunk(text=f"{title_prefix}{c.text}", index=c.index, metadata=c.metadata)
                for c in chunks
            ]
    if not chunks:
        return

    now = datetime.now().isoformat()

    block_rows = []
    chunk_rows = []
    for c in chunks:
        block_id = str(uuid.uuid4())
        block_rows.append({
            "id": block_id,
            "parent_id": None,
            "page_id": item.id,
            "content": c.text,
            "block_type": "text",
            "properties": json.dumps(
                {
                    **c.metadata,
                    "knowledge_id": item.id,
                    "chunk_index": c.index,
                },
                ensure_ascii=False,
            ),
            "order_idx": c.index,
            "created_at": now,
            "updated_at": now,
        })
        chunk = KnowledgeChunk(
            knowledge_id=item.id, chunk_index=c.index, chunk_text=c.text
        )
        row = chunk.to_row()
        row["id"] = block_id
        chunk_rows.append(row)

    # 内容质量校验
    quality_score, quality_warnings = _validate_content_quality(block_rows)
    if quality_warnings:
        logger.warning(
            f"Low quality content for {item.id} (score={quality_score}): {quality_warnings}"
        )

    # 写入 quality_score 到 knowledge_items（如果字段存在）
    try:
        Database.update_knowledge(item.id, quality_score=quality_score)
    except Exception:
        pass  # quality_score 字段可能不存在（迁移前）

    Database.insert_chunks(chunk_rows)

    Database.insert_blocks(block_rows)

    texts: list[str] = [str(b["content"]) for b in block_rows]
    embeddings: list[list[float] | None] = [None] * len(texts)
    try:
        from src.services.embedding import EmbeddingService
        embedding_service = EmbeddingService()
        build_embedding_text = getattr(embedding_service, "build_embedding_text", None)
        if callable(build_embedding_text):
            texts = [str(build_embedding_text(b)) for b in block_rows]
        else:
            texts = [str(b["content"]) for b in block_rows]
        embeddings = list(embedding_service.embed_batch_with_cache(texts))
        if len(embeddings) != len(texts):
            logging.warning(
                "Embedding count mismatch for %s: expected %d, got %d. "
                "Missing embeddings will be unsearchable via vector search.",
                item.id, len(texts), len(embeddings),
            )
    except Exception as e:
        logging.error(f"Embedding failed for {item.id}: {e}")

    # 批量写入向量（低质量文档quality_score==0时跳过向量索引，但保留FTS）
    # quality_score 为 None 时视为有效文档（未评分），默认写入向量
    if quality_score is None or quality_score > 0:
        valid_block_ids = []
        valid_embeddings = []
        for i, block in enumerate(block_rows):
            emb = embeddings[i] if i < len(embeddings) else None
            if emb:
                valid_block_ids.append(str(block["id"]))
                valid_embeddings.append(emb)
        if valid_block_ids:
            try:
                BlockStore().add_block_embeddings_batch(valid_block_ids, valid_embeddings)
            except Exception as e:
                logger.error(f"Batch vec insert failed for {item.id}: {e}")
                # 回退: 逐条插入
                for bid, emb in zip(valid_block_ids, valid_embeddings):
                    try:
                        BlockStore().add_block_embedding(bid, emb)
                    except Exception as e2:
                        logger.error(f"Vec insert failed for block {bid}: {e2}")
        # Legacy chunk vectors（Phase 2: 默认关闭，通过 rag.legacy_chunk_vector 可恢复）
        if Config.get("rag.legacy_chunk_vector", False):
            for i, block in enumerate(block_rows):
                emb = embeddings[i] if i < len(embeddings) else None
                if emb:
                    try:
                        VectorStore().add_chunk_embedding(chunk_rows[i]["id"], item.id, emb)
                    except Exception as e:
                        logger.error(f"Legacy vec insert failed for chunk {chunk_rows[i]['id']}: {e}")
    else:
        logger.info(f"Skipping vector index for low-quality item {item.id} (score={quality_score})")

    try:
        Database.insert_blocks_fts(block_rows)
    except Exception as e:
        logging.error(f"Block FTS insert failed for {item.id}: {e}")

    chunk_dicts = [{"id": chunk_rows[i]["id"], "knowledge_id": item.id,
                    "chunk_text": c.text} for i, c in enumerate(chunks)]
    try:
        Database.insert_chunks_fts(chunk_dicts)
    except Exception as e:
        logging.error(f"Legacy chunk FTS insert failed for {item.id}: {e}")


def reindex_knowledge_item(item_id: str, item: KnowledgeItem):
    """删除旧索引，重新分块索引"""
    BlockStore().delete_by_page(item_id)
    if Config.get("rag.legacy_chunk_vector", False):
        VectorStore().delete_by_knowledge(item_id)
    Database.delete_blocks_by_page(item_id)
    Database.delete_chunks_fts(item_id)
    Database.delete_chunks(item_id)
    index_knowledge_item(item)


def _cleanup_orphan_vectors():
    """清理 vec_blocks 中 block_id 已不存在的孤儿向量"""
    with Database._write_lock:
        conn = Database.get_conn()
        result = conn.execute(
            "DELETE FROM vec_blocks WHERE rowid NOT IN (SELECT rowid FROM blocks)"
        )
        conn.commit()
        removed = result.rowcount
    if removed > 0:
        logger.info(f"Cleaned up {removed} orphan vectors from vec_blocks")
    return removed


_VALID_JOURNAL_MODES = {"DELETE", "WAL", "TRUNCATE", "PERSIST", "MEMORY"}


def _set_journal_mode(mode: str) -> str:
    """切换 SQLite journal_mode，返回之前的模式"""
    if mode.upper() not in _VALID_JOURNAL_MODES:
        raise ValueError(f"Invalid journal_mode: {mode}")
    with Database._write_lock:
        conn = Database.get_conn()
        old_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if old_mode.lower() != mode.lower():
            conn.execute(f"PRAGMA journal_mode={mode}")
            logger.info(f"Switched journal_mode: {old_mode} → {mode}")
    return old_mode


def reindex_all(
    progress_callback: Callable[[int, int, str], None] | None = None,
    dry_run: bool = False,
    restart: bool = True,
    batch_size: int = 64,
) -> dict:
    """重建所有知识条目的索引（向量 + FTS）

    增强:
    - 断点续传: restart=True 时从上次中断位置继续（基于 async_jobs 记录）
    - WAL模式: reindex期间切WAL不阻塞读，完成后切回DELETE
    - 孤儿清理: reindex前清理vec_blocks中的孤儿向量
    - 批量向量写入: 使用 add_block_embeddings_batch 减少 commit 次数
    """
    items = Database.list_knowledge(limit=100000)
    total = len(items)
    if dry_run:
        rows = Database.get_conn().execute(
            "SELECT page_id, COUNT(*) AS cnt FROM blocks GROUP BY page_id"
        ).fetchall()
        block_counts = {row["page_id"]: row["cnt"] for row in rows}
        affected_blocks = sum(block_counts.get(row["id"], 0) for row in items)
        emb_batch_size = int(Config.get("embedding.batch_size", 20) or 20)
        estimated_batches = (
            (affected_blocks + emb_batch_size - 1) // emb_batch_size
            if affected_blocks else 0
        )
        return {
            "affected_items": total,
            "affected_blocks": affected_blocks,
            "embedding_context_enabled": bool(
                Config.get("rag.embedding_context.enabled", False)
            ),
            "estimated_batches": estimated_batches,
        }

    # 断点续传: 从 async_jobs 获取已处理的 item_id 集合
    processed_ids = set()
    if restart:
        try:
            conn = Database.get_conn()
            job_rows = conn.execute(
                "SELECT params FROM async_jobs WHERE job_type = 'reindex_all' "
                "AND status IN ('completed', 'processing') ORDER BY created_at DESC"
            ).fetchall()
            for jr in job_rows:
                try:
                    meta = json.loads(jr["params"]) if isinstance(jr["params"], str) else jr["params"]
                    if isinstance(meta, dict) and "processed_ids" in meta:
                        processed_ids.update(meta["processed_ids"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if processed_ids:
                logger.info(f"Resuming reindex: {len(processed_ids)} items already processed")
        except Exception as e:
            logger.debug(f"Could not load reindex checkpoint: {e}")

    # 孤儿清理
    orphans_removed = _cleanup_orphan_vectors()

    # 切换到 WAL 模式
    old_journal_mode = _set_journal_mode("WAL")

    success = 0
    failed = 0
    errors = []
    skipped = 0
    processed_this_run: set[str] = set()

    try:
        for i, row in enumerate(items):
            # 断点续传: 跳过已处理
            if row["id"] in processed_ids:
                skipped += 1
                continue
            try:
                tags_raw = row.get("tags", "[]")
                if isinstance(tags_raw, str):
                    try:
                        tags = json.loads(tags_raw)
                    except (json.JSONDecodeError, TypeError):
                        tags = []
                else:
                    tags = tags_raw if isinstance(tags_raw, list) else []

                item = KnowledgeItem(
                    id=row["id"],
                    title=row["title"],
                    content=row.get("content", ""),
                    tags=tags,
                    source_type=row.get("source_type", "manual"),
                    source_path=row.get("source_path", ""),
                    file_type=row.get("file_type", "txt"),
                )
                reindex_knowledge_item(row["id"], item)
                success += 1
                processed_this_run.add(row["id"])

                # 定期保存断点（每10个item）
                if len(processed_this_run) % 10 == 0:
                    _save_reindex_checkpoint(list(processed_ids | processed_this_run))

                if progress_callback:
                    progress_callback(success + skipped, total, f"Reindexing {success + skipped}/{total}")
                elif (success) % 10 == 0:
                    logger.info(f"Reindex progress: {success + skipped}/{total} (success={success}, skipped={skipped})")
            except Exception as e:
                failed += 1
                errors.append({"id": row["id"], "title": row["title"], "error": str(e)})
                logger.error(f"Reindex failed for {row.get('title', '')}: {e}")
    finally:
        # 切回原始 journal 模式
        _set_journal_mode(old_journal_mode)

    return {
        "total": total,
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "orphans_removed": orphans_removed,
        "errors": errors[:10],
    }


def _save_reindex_checkpoint(processed_ids: list[str]):
    """保存 reindex 断点到 async_jobs 表"""
    try:
        conn = Database.get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO async_jobs (id, job_type, status, params, created_at) "
            "VALUES ('reindex_checkpoint', 'reindex_all', 'processing', ?, datetime('now'))",
            (json.dumps({"processed_ids": processed_ids[-500:]}),),  # 只保留最近500个避免过大
        )
        conn.commit()
    except Exception as e:
        logger.debug(f"Could not save reindex checkpoint: {e}")


class IndexerService:
    """Indexer 服务类 — 封装 indexer 函数，供 Container DI 使用"""

    def __init__(self, db, vectorstore, embedding, config):
        self._db = db
        self._vectorstore = vectorstore
        self._embedding = embedding
        self._config = config

    def index(self, item):
        index_knowledge_item(item)

    def reindex(self, item_id: str, item):
        reindex_knowledge_item(item_id, item)

    def reindex_all(self, progress_callback=None, dry_run: bool = False):
        return reindex_all(progress_callback, dry_run=dry_run)

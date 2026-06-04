"""知识条目索引 — Block-First 管线（分块 + 向量化 + 全文索引）"""
import json
import logging
import uuid
from datetime import datetime

from src.utils.config import Config
from src.models.knowledge import KnowledgeItem, KnowledgeChunk
from src.services.db import Database
from src.services.text_splitter import TextChunk, split_text, split_markdown, split_code
from src.services.block_store import BlockStore
from src.services.vectorstore import VectorStore


def index_knowledge_item(item: KnowledgeItem):
    """将知识条目分块并存入 DB + 向量库 + 全文索引（Block-First 管线）"""
    tags_str = ",".join(item.tags)
    chunk_size = Config.get("rag.chunk_size", 500)
    chunk_overlap = Config.get("rag.chunk_overlap", 50)
    base_meta = {"knowledge_id": item.id, "tags": tags_str, "title": item.title,
                 "created_at": item.created_at}

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
            "properties": json.dumps({
                "knowledge_id": item.id,
                "chunk_index": c.index,
            }, ensure_ascii=False),
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

    Database.insert_blocks(block_rows)

    Database.insert_chunks(chunk_rows)

    texts = [b["content"] for b in block_rows]
    embeddings = [None] * len(texts)
    try:
        from src.services.embedding import EmbeddingService
        embedding_service = EmbeddingService()
        texts = [embedding_service.build_embedding_text(b) for b in block_rows]
        embeddings = embedding_service.embed_batch_with_cache(texts)
        if len(embeddings) != len(texts):
            logging.warning(
                "Embedding count mismatch for %s: expected %d, got %d. "
                "Missing embeddings will be unsearchable via vector search.",
                item.id, len(texts), len(embeddings),
            )
    except Exception as e:
        logging.error(f"Embedding failed for {item.id}: {e}")

    for i, block in enumerate(block_rows):
        emb = embeddings[i] if i < len(embeddings) else None
        if emb:
            try:
                BlockStore().add_block_embedding(block["id"], emb)
            except Exception as e:
                logging.error(f"Vec insert failed for block {block['id']}: {e}")
            try:
                VectorStore().add_chunk_embedding(chunk_rows[i]["id"], item.id, emb)
            except Exception as e:
                logging.error(f"Legacy vec insert failed for chunk {chunk_rows[i]['id']}: {e}")

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
    VectorStore().delete_by_knowledge(item_id)
    Database.delete_blocks_by_page(item_id)
    Database.delete_chunks_fts(item_id)
    Database.delete_chunks(item_id)
    index_knowledge_item(item)


def reindex_all(progress_callback: callable = None, dry_run: bool = False) -> dict:
    """重建所有知识条目的索引（向量 + FTS）"""
    items = Database.list_knowledge(limit=100000)
    total = len(items)
    if dry_run:
        rows = Database.get_conn().execute(
            "SELECT page_id, COUNT(*) AS cnt FROM blocks GROUP BY page_id"
        ).fetchall()
        block_counts = {row["page_id"]: row["cnt"] for row in rows}
        affected_blocks = sum(block_counts.get(row["id"], 0) for row in items)
        batch_size = int(Config.get("embedding.batch_size", 20) or 20)
        estimated_batches = (
            (affected_blocks + batch_size - 1) // batch_size
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
    success = 0
    failed = 0
    errors = []

    for i, row in enumerate(items):
        try:
            tags_raw = row.get("tags", "[]")
            if isinstance(tags_raw, str):
                import json
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
            if progress_callback:
                progress_callback(i + 1, total, f"Reindexing {i+1}/{total}")
            elif (i + 1) % 10 == 0:
                logging.info(f"Reindex progress: {i + 1}/{total}")
        except Exception as e:
            failed += 1
            errors.append({"id": row["id"], "title": row["title"], "error": str(e)})
            logging.error(f"Reindex failed for {row.get('title', '')}: {e}")

    return {"total": total, "success": success, "failed": failed, "errors": errors[:10]}


class IndexerService:
    """Indexer 服务类 — 封装 indexer 函数，供 Container DI 使用"""

    def __init__(self, db, vectorstore, embedding, config):
        self._db = db
        self._vectorstore = vectorstore
        self._embedding = embedding
        self._config = config

    def index(self, item):
        from src.models.knowledge import KnowledgeItem
        index_knowledge_item(item)

    def reindex(self, item_id: str, item):
        reindex_knowledge_item(item_id, item)

    def reindex_all(self, progress_callback=None, dry_run: bool = False):
        return reindex_all(progress_callback, dry_run=dry_run)

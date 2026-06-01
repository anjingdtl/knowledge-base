"""知识条目索引 — 分块 + 向量化 + 全文索引（原子同步写入）"""
import logging
from src.utils.config import Config
from src.models.knowledge import KnowledgeItem, KnowledgeChunk
from src.services.db import Database
from src.services.text_splitter import split_text, split_markdown, split_code
from src.services.vectorstore import VectorStore


def index_knowledge_item(item: KnowledgeItem):
    """将知识条目分块并存入 DB + 向量库 + 全文索引（同步写入）"""
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
    if not chunks:
        return

    chunk_rows = []
    for c in chunks:
        chunk = KnowledgeChunk(knowledge_id=item.id, chunk_index=c.index, chunk_text=c.text)
        chunk_rows.append(chunk.to_row())

    Database.insert_chunks(chunk_rows)

    # 计算所有 chunk 的 embedding（批量调用 API）
    texts = [c.text for c in chunks]
    embeddings = [None] * len(texts)
    try:
        from src.services.embedding import EmbeddingService
        embeddings = EmbeddingService().embed_batch(texts)
        if len(embeddings) != len(texts):
            logging.warning(
                "Embedding count mismatch for %s: expected %d, got %d. "
                "Missing embeddings will be unsearchable via vector search.",
                item.id, len(texts), len(embeddings),
            )
    except Exception as e:
        logging.error(f"Embedding failed for {item.id}: {e}")

    # 写入 vec_chunks（向量）
    for i, c in enumerate(chunks):
        emb = embeddings[i] if i < len(embeddings) else None
        if emb:
            try:
                VectorStore().add_chunk_embedding(chunk_rows[i]["id"], item.id, emb)
            except Exception as e:
                logging.error(f"Vec insert failed for chunk {chunk_rows[i]['id']}: {e}")

    # 写入 chunk_fts（全文索引）
    chunk_dicts = [{"id": chunk_rows[i]["id"], "knowledge_id": item.id,
                    "chunk_text": c.text} for i, c in enumerate(chunks)]
    try:
        Database.insert_chunks_fts(chunk_dicts)
    except Exception as e:
        logging.error(f"FTS insert failed for {item.id}: {e}")


def reindex_knowledge_item(item_id: str, item: KnowledgeItem):
    """删除旧索引，重新分块索引"""
    VectorStore().delete_by_knowledge(item_id)
    Database.delete_chunks_fts(item_id)
    Database.delete_chunks(item_id)
    index_knowledge_item(item)


def reindex_all(progress_callback: callable = None) -> dict:
    """重建所有知识条目的索引（向量 + FTS）"""
    items = Database.list_knowledge(limit=100000)
    total = len(items)
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

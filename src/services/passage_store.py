"""Retrieval passage store: SQL + FTS + sqlite-vec (SPEC v3 §A/E).

Passages are independent of graph blocks. Block vectors remain for structure;
semantic retrieval must use this store.
"""
from __future__ import annotations

import json
import logging
import struct
import threading
from typing import Any, Callable

import sqlite_vec

from src.services.passage_builder import (
    PassageDraft,
    build_passages_for_document,
    passages_to_rows,
)
from src.utils.config import Config

logger = logging.getLogger(__name__)
_lock = threading.Lock()

# DDL also applied at runtime ensure for DBs that lag Alembic briefly.
_PASSAGE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS retrieval_passages (
    id TEXT PRIMARY KEY,
    knowledge_id TEXT NOT NULL,
    document_family_id TEXT DEFAULT '',
    family_confidence REAL DEFAULT 0,
    family_basis TEXT DEFAULT '',
    source_version TEXT DEFAULT '',
    version_year INTEGER,
    passage_index INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL DEFAULT '',
    text_hash TEXT DEFAULT '',
    char_count INTEGER DEFAULT 0,
    short_passage INTEGER DEFAULT 0,
    title_prefix TEXT DEFAULT '',
    section_path TEXT DEFAULT '',
    block_ids_json TEXT DEFAULT '[]',
    block_ranges_json TEXT DEFAULT '[]',
    effective_year INTEGER,
    status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT,
    deleted_at TEXT
)
"""


class PassageStore:
    """CRUD + vector/FTS for retrieval_passages."""

    _instance = None
    _initialized = False
    _lock = threading.Lock()

    def __new__(cls, db=None):
        if db is not None:
            inst = super().__new__(cls)
            inst._initialized = False
            inst._db = db
            return inst
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
                cls._instance._db = None
            return cls._instance

    def _get_db(self):
        if self._db is not None:
            return self._db
        from src.services.db import Database
        return Database

    def _get_conn(self):
        # Mirror BlockStore: prefer injected db; else public Database.get_conn() facade.
        # Do not touch private singleton fields; architecture debt gate requires public API only.
        if self._db is not None:
            if hasattr(self._db, "get_conn"):
                return self._db.get_conn()
            return self._db
        from src.services.db import Database
        return Database.get_conn()

    def _get_dimension(self) -> int:
        return int(Config.get("embedding.dimension", 1024))

    def ensure_schema(self) -> None:
        """Idempotent schema ensure (table + FTS + vec). Safe to call often."""
        if self._initialized:
            return
        with _lock:
            if self._initialized:
                return
            conn = self._get_conn()
            conn.execute(_PASSAGE_TABLE_SQL)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_retrieval_passages_kid "
                "ON retrieval_passages(knowledge_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_retrieval_passages_family "
                "ON retrieval_passages(document_family_id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_retrieval_passages_kid_idx "
                "ON retrieval_passages(knowledge_id, passage_index)"
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS passage_fts USING fts5(
                    fts_segmented,
                    knowledge_id UNINDEXED,
                    passage_id UNINDEXED,
                    tokenize='unicode61'
                )
                """
            )
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
            finally:
                try:
                    conn.enable_load_extension(False)
                except Exception:
                    pass
            dim = self._get_dimension()
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_passages USING vec0("
                f"embedding float[{dim}] distance_metric=cosine)"
            )
            conn.commit()
            self._initialized = True

    def _pack_embedding(self, embedding: list[float]) -> bytes:
        return struct.pack(f"{len(embedding)}f", *embedding)

    def _segment(self, text: str) -> str:
        try:
            from src.utils.chinese_tokenizer import tokenize_chinese_full
            return tokenize_chinese_full(text or "")
        except Exception:
            try:
                import jieba
                return " ".join(jieba.cut_for_search(text or ""))
            except Exception:
                return text or ""

    # ------------------------------------------------------------------ write
    def delete_by_knowledge(self, knowledge_id: str) -> None:
        self.ensure_schema()
        conn = self._get_conn()
        kid = knowledge_id
        rows = conn.execute(
            "SELECT id, rowid FROM retrieval_passages WHERE knowledge_id = ?",
            (kid,),
        ).fetchall()
        if not rows:
            conn.execute("DELETE FROM passage_fts WHERE knowledge_id = ?", (kid,))
            conn.commit()
            return
        ids = [r[0] if not hasattr(r, "keys") else r["id"] for r in rows]
        rowids = [r[1] if not hasattr(r, "keys") else r["rowid"] for r in rows]
        # vec_passages keyed by passage rowid
        for batch_start in range(0, len(rowids), 500):
            batch = rowids[batch_start:batch_start + 500]
            ph = ",".join("?" for _ in batch)
            try:
                conn.execute(f"DELETE FROM vec_passages WHERE rowid IN ({ph})", batch)
            except Exception as e:
                logger.debug("vec_passages delete batch: %s", e)
        conn.execute("DELETE FROM passage_fts WHERE knowledge_id = ?", (kid,))
        conn.execute("DELETE FROM retrieval_passages WHERE knowledge_id = ?", (kid,))
        conn.commit()
        logger.debug("Deleted %d passages for knowledge %s", len(ids), kid)

    def upsert_passages(
        self,
        rows: list[dict[str, Any]],
        *,
        embeddings: list[list[float]] | None = None,
    ) -> int:
        self.ensure_schema()
        if not rows:
            return 0
        conn = self._get_conn()
        sql = """
            INSERT OR REPLACE INTO retrieval_passages (
                id, knowledge_id, document_family_id, family_confidence, family_basis,
                source_version, version_year, passage_index, text, text_hash,
                char_count, short_passage, title_prefix, section_path,
                block_ids_json, block_ranges_json, effective_year, status,
                created_at, updated_at, deleted_at
            ) VALUES (
                :id, :knowledge_id, :document_family_id, :family_confidence, :family_basis,
                :source_version, :version_year, :passage_index, :text, :text_hash,
                :char_count, :short_passage, :title_prefix, :section_path,
                :block_ids_json, :block_ranges_json, :effective_year, :status,
                :created_at, :updated_at, :deleted_at
            )
        """
        for row in rows:
            conn.execute(sql, row)
            seg = self._segment(row.get("text") or "")
            conn.execute(
                "INSERT INTO passage_fts(fts_segmented, knowledge_id, passage_id) "
                "VALUES (?, ?, ?)",
                (seg, row["knowledge_id"], row["id"]),
            )
        conn.commit()

        if embeddings:
            self.add_embeddings_batch(
                [r["id"] for r in rows],
                embeddings,
            )
        return len(rows)

    def add_embedding(self, passage_id: str, embedding: list[float]) -> None:
        self.add_embeddings_batch([passage_id], [embedding])

    def add_embeddings_batch(
        self,
        passage_ids: list[str],
        embeddings: list[list[float]],
    ) -> int:
        self.ensure_schema()
        if not passage_ids or not embeddings:
            return 0
        expected = self._get_dimension()
        conn = self._get_conn()
        inserted = 0
        for batch_start in range(0, len(passage_ids), 500):
            batch_ids = passage_ids[batch_start:batch_start + 500]
            batch_embs = embeddings[batch_start:batch_start + 500]
            ph = ",".join("?" for _ in batch_ids)
            rowid_map = dict(conn.execute(
                f"SELECT id, rowid FROM retrieval_passages WHERE id IN ({ph})",
                batch_ids,
            ).fetchall())
            pairs = []
            for pid, emb in zip(batch_ids, batch_embs):
                if not emb or len(emb) != expected:
                    continue
                rid = rowid_map.get(pid)
                if rid is None:
                    continue
                pairs.append((rid, self._pack_embedding(emb)))
            if not pairs:
                continue
            conn.executemany(
                "INSERT OR REPLACE INTO vec_passages(rowid, embedding) VALUES (?, ?)",
                pairs,
            )
            conn.commit()
            inserted += len(pairs)
        return inserted

    def rebuild_for_knowledge(
        self,
        *,
        knowledge_id: str,
        title: str = "",
        content: str = "",
        blocks: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        embedding_service: Any = None,
        embed: bool = True,
    ) -> list[dict[str, Any]]:
        """Delete old passages for knowledge_id and rebuild from source."""
        self.ensure_schema()
        self.delete_by_knowledge(knowledge_id)
        drafts = build_passages_for_document(
            knowledge_id=knowledge_id,
            title=title,
            content=content,
            blocks=blocks or [],
            metadata=metadata,
        )
        rows = passages_to_rows(drafts)
        if not rows:
            return []
        embeddings = None
        if embed:
            try:
                emb = embedding_service
                if emb is None:
                    from src.services.embedding import EmbeddingService
                    emb = EmbeddingService()
                texts = [r["text"] for r in rows]
                embeddings = emb.embed_batch_with_cache(texts)
            except Exception as e:
                logger.warning("Passage embedding failed for %s: %s", knowledge_id, e)
                embeddings = None
        self.upsert_passages(rows, embeddings=embeddings)
        return rows

    # ------------------------------------------------------------------- read
    def get_by_knowledge(self, knowledge_id: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM retrieval_passages "
            "WHERE knowledge_id = ? AND (deleted_at IS NULL OR deleted_at = '') "
            "ORDER BY passage_index",
            (knowledge_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, passage_id: str) -> dict[str, Any] | None:
        self.ensure_schema()
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM retrieval_passages WHERE id = ?",
            (passage_id,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def vector_search(
        self,
        query: str,
        top_k: int = 10,
        *,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_schema()
        if query_embedding is None:
            from src.services.embedding import EmbeddingService
            query_embedding = EmbeddingService().embed(query)
        if not query_embedding:
            return []
        conn = self._get_conn()
        packed = self._pack_embedding(query_embedding)
        try:
            rows = conn.execute(
                """
                SELECT p.id, p.knowledge_id, p.text, p.title_prefix, p.section_path,
                       p.document_family_id, p.version_year, p.source_version,
                       p.block_ids_json, p.block_ranges_json, p.passage_index,
                       p.char_count, p.short_passage, p.family_basis, p.family_confidence,
                       vc.distance
                FROM vec_passages vc
                JOIN retrieval_passages p ON p.rowid = vc.rowid
                LEFT JOIN knowledge_items ki ON ki.id = p.knowledge_id
                WHERE vc.embedding MATCH ? AND k = ?
                  AND (p.deleted_at IS NULL OR p.deleted_at = '')
                  AND (ki.id IS NULL OR ki.deleted_at IS NULL)
                ORDER BY vc.distance
                """,
                (packed, top_k),
            ).fetchall()
        except Exception as e:
            logger.warning("passage vector search failed: %s", e)
            return []
        return [self._hit_from_row(r, channel="semantic") for r in rows]

    def fts_search(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        self.ensure_schema()
        conn = self._get_conn()
        q = (query or "").strip()
        if not q:
            return []
        try:
            from src.utils.chinese_tokenizer import (
                sanitize_fts_query,
                tokenize_chinese_full,
            )
            segmented = tokenize_chinese_full(q)
            match = sanitize_fts_query(segmented, is_tokenized=True) if segmented.strip() else ""
        except Exception:
            match = q
        if not match:
            match = q
        try:
            rows = conn.execute(
                """
                SELECT p.id, p.knowledge_id, p.text, p.title_prefix, p.section_path,
                       p.document_family_id, p.version_year, p.source_version,
                       p.block_ids_json, p.block_ranges_json, p.passage_index,
                       p.char_count, p.short_passage, p.family_basis, p.family_confidence,
                       bm25(passage_fts) AS rank
                FROM passage_fts
                JOIN retrieval_passages p ON p.id = passage_fts.passage_id
                LEFT JOIN knowledge_items ki ON ki.id = p.knowledge_id
                WHERE passage_fts MATCH ?
                  AND (p.deleted_at IS NULL OR p.deleted_at = '')
                  AND (ki.id IS NULL OR ki.deleted_at IS NULL)
                ORDER BY rank
                LIMIT ?
                """,
                (match, top_k),
            ).fetchall()
        except Exception as e:
            logger.debug("passage FTS failed (%s); fallback LIKE", e)
            like = f"%{q}%"
            rows = conn.execute(
                """
                SELECT p.id, p.knowledge_id, p.text, p.title_prefix, p.section_path,
                       p.document_family_id, p.version_year, p.source_version,
                       p.block_ids_json, p.block_ranges_json, p.passage_index,
                       p.char_count, p.short_passage, p.family_basis, p.family_confidence,
                       0 AS rank
                FROM retrieval_passages p
                LEFT JOIN knowledge_items ki ON ki.id = p.knowledge_id
                WHERE p.text LIKE ?
                  AND (p.deleted_at IS NULL OR p.deleted_at = '')
                  AND (ki.id IS NULL OR ki.deleted_at IS NULL)
                LIMIT ?
                """,
                (like, top_k),
            ).fetchall()
        return [self._hit_from_row(r, channel="keyword", fts=True) for r in rows]

    def count(self) -> int:
        self.ensure_schema()
        row = self._get_conn().execute(
            "SELECT COUNT(*) FROM retrieval_passages "
            "WHERE deleted_at IS NULL OR deleted_at = ''"
        ).fetchone()
        return int(row[0] if row else 0)

    def vector_count(self) -> int:
        self.ensure_schema()
        try:
            row = self._get_conn().execute(
                "SELECT COUNT(*) FROM vec_passages"
            ).fetchone()
            return int(row[0] if row else 0)
        except Exception:
            return 0

    def fts_count(self) -> int:
        self.ensure_schema()
        try:
            row = self._get_conn().execute(
                "SELECT COUNT(*) FROM passage_fts"
            ).fetchone()
            return int(row[0] if row else 0)
        except Exception:
            return 0

    def health_stats(self) -> dict[str, Any]:
        """Passage index health for kb_capabilities / diagnostics (SPEC §E)."""
        self.ensure_schema()
        conn = self._get_conn()
        total = self.count()
        vectors = self.vector_count()
        fts = self.fts_count()
        short_n = 0
        lengths: list[int] = []
        try:
            rows = conn.execute(
                """
                SELECT char_count, short_passage FROM retrieval_passages
                WHERE (deleted_at IS NULL OR deleted_at = '')
                """
            ).fetchall()
            for r in rows:
                clen = int(r[0] if not hasattr(r, "keys") else (r["char_count"] or 0))
                lengths.append(clen)
                sp = r[1] if not hasattr(r, "keys") else r["short_passage"]
                if sp:
                    short_n += 1
        except Exception as e:
            logger.debug("health length scan: %s", e)

        def _pct(p: float) -> float:
            if not lengths:
                return 0.0
            s = sorted(lengths)
            idx = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
            return float(s[idx])

        non_short = [x for x in lengths if x >= SHORT_PASSAGE_THRESHOLD_SAFE]
        avg = (sum(non_short) / len(non_short)) if non_short else (sum(lengths) / len(lengths) if lengths else 0.0)
        cov_v = (vectors / total) if total else 1.0
        cov_f = (fts / total) if total else 1.0
        # Length gates (SPEC §E.4) apply to non-short passages.
        ns = sorted(non_short) if non_short else sorted(lengths)
        p50 = _pct_from(ns, 50) if ns else 0.0
        p95 = _pct_from(ns, 95) if ns else 0.0
        length_ok = (
            (not ns)
            or (avg >= 300 and p50 >= 250 and p95 <= 1300)
        )
        return {
            "retrieval_index_unit": "passage",
            "passages": total,
            "embedded": vectors,
            "fts": fts,
            "vector_coverage": round(cov_v, 4),
            "fts_coverage": round(cov_f, 4),
            "avg_char_count": round(avg, 1),
            "p50_char_count": p50,
            "p95_char_count": p95,
            "short_passage_count": short_n,
            "length_gate_ok": bool(length_ok),
            "blocks_note": "blocks/vectors reported separately; not retrieval unit",
        }

    def rebuild_all(
        self,
        *,
        progress_callback: Callable[[int, int, str], None] | None = None,
        embed: bool = True,
        knowledge_ids: list[str] | None = None,
        embed_batch_size: int = 8,
        embed_timeout: float = 120.0,
        rebuild_text: bool = True,
    ) -> dict[str, Any]:
        """Rebuild passages for all (or selected) knowledge items.

        Strategy (SPEC v3 rebuild reliability):
          1. Build + write all passages without embedding (fast, deterministic)
             unless ``rebuild_text=False`` (resume embed only).
          2. Batch-embed missing passage vectors with raised timeout and small
             batches so process-isolation deadlines do not kill multi-passage docs.
        """
        self.ensure_schema()
        from src.services.db import Database

        db = self._get_db() if self._db is not None else Database
        if knowledge_ids is not None:
            items = []
            for kid in knowledge_ids:
                it = db.get_knowledge(kid) if hasattr(db, "get_knowledge") else None
                if it:
                    items.append(it)
        else:
            items = db.list_knowledge(limit=100000)
        total = len(items)
        built = 0
        failed = 0
        errors: list[dict[str, str]] = []
        all_rows: list[dict[str, Any]] = []

        if rebuild_text:
            for i, item in enumerate(items):
                kid = item.get("id") if isinstance(item, dict) else getattr(item, "id", "")
                title = item.get("title") if isinstance(item, dict) else getattr(item, "title", "")
                content = item.get("content") if isinstance(item, dict) else getattr(item, "content", "")
                try:
                    blocks = []
                    if hasattr(db, "get_blocks_by_page"):
                        blocks = db.get_blocks_by_page(kid) or []
                    elif hasattr(db, "list_blocks"):
                        blocks = db.list_blocks(kid) or []
                    else:
                        conn = self._get_conn()
                        blocks = [
                            dict(r) if hasattr(r, "keys") else {
                                "id": r[0], "content": r[1], "order_idx": r[2],
                            }
                            for r in conn.execute(
                                "SELECT id, content, order_idx FROM blocks "
                                "WHERE page_id = ? ORDER BY order_idx",
                                (kid,),
                            ).fetchall()
                        ]
                    # Phase 1: text + FTS only (no embed) for reliability.
                    rows = self.rebuild_for_knowledge(
                        knowledge_id=kid,
                        title=title or "",
                        content=content or "",
                        blocks=blocks,
                        embed=False,
                    )
                    built += len(rows)
                    all_rows.extend(rows)
                except Exception as e:
                    failed += 1
                    errors.append({"knowledge_id": kid, "error": str(e)[:300]})
                    logger.error("passage rebuild failed for %s: %s", kid, e)
                if progress_callback:
                    progress_callback(i + 1, total, kid)
        else:
            # Resume: load existing passage rows from DB.
            conn = self._get_conn()
            for r in conn.execute(
                "SELECT * FROM retrieval_passages "
                "WHERE deleted_at IS NULL OR deleted_at = '' "
                "ORDER BY knowledge_id, passage_index"
            ).fetchall():
                all_rows.append(self._row_to_dict(r))
            built = len(all_rows)

        embed_errors: list[dict[str, str]] = []
        embedded = 0
        if embed and all_rows:
            embedded, embed_errors = self._embed_all_passages(
                all_rows,
                batch_size=embed_batch_size,
                timeout=embed_timeout,
                progress_callback=progress_callback,
            )

        stats = self.health_stats()
        return {
            "items": total,
            "passages_built": built,
            "failed_items": failed,
            "embedded": embedded,
            "embed_errors": embed_errors[:50],
            "errors": errors[:50],
            "health": stats,
        }

    def _embed_all_passages(
        self,
        rows: list[dict[str, Any]],
        *,
        batch_size: int = 8,
        timeout: float = 120.0,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[int, list[dict[str, str]]]:
        """Embed passages in small batches with elevated timeout."""
        from src.utils.config import Config

        # Temporarily raise embedding timeout for rebuild (process isolation).
        old_timeout = Config.get("embedding.timeout", 15)
        try:
            if hasattr(Config, "set"):
                Config.set("embedding.timeout", timeout)
            else:
                # Fallback: mutate in-memory config dict if present.
                cfg = getattr(Config, "_data", None) or getattr(Config, "_config", None)
                if isinstance(cfg, dict):
                    emb = cfg.setdefault("embedding", {})
                    if isinstance(emb, dict):
                        emb["timeout"] = timeout
        except Exception as e:
            logger.debug("Could not raise embedding.timeout: %s", e)

        try:
            from src.services.embedding import EmbeddingService
            emb = EmbeddingService()
            # Force client rebuild with new timeout.
            emb._client = None  # type: ignore[attr-defined]
            # Rebuilds are offline batch jobs: prefer async isolation over
            # process-spawn-per-batch (much faster for multi-thousand passages).
            old_iso = getattr(EmbeddingService, "ISOLATION_MODE", "process")
            try:
                EmbeddingService.ISOLATION_MODE = "async"  # type: ignore[assignment]
            except Exception:
                old_iso = None
        except Exception as e:
            logger.warning("EmbeddingService unavailable for passage embed: %s", e)
            return 0, [{"error": str(e)}]

        errors: list[dict[str, str]] = []
        embedded = 0
        size = max(1, int(batch_size))
        total = len(rows)
        # Skip passages that already have vectors (resume-friendly).
        try:
            conn = self._get_conn()
            existing = {
                r[0]
                for r in conn.execute(
                    """
                    SELECT p.id FROM retrieval_passages p
                    JOIN vec_passages v ON v.rowid = p.rowid
                    """
                ).fetchall()
            }
        except Exception:
            existing = set()
        pending = [r for r in rows if r.get("id") not in existing]
        logger.info(
            "Passage embed: %d total, %d already embedded, %d pending",
            total, len(existing), len(pending),
        )
        total = len(pending)
        for offset in range(0, total, size):
            batch = pending[offset:offset + size]
            ids = [r["id"] for r in batch]
            texts = [r.get("text") or "" for r in batch]
            try:
                vectors = emb.embed_batch_with_cache(texts, batch_size=len(texts))
                if len(vectors) != len(ids):
                    raise RuntimeError(
                        f"embed count mismatch: {len(vectors)} != {len(ids)}"
                    )
                n = self.add_embeddings_batch(ids, vectors)
                embedded += n
            except Exception as e:
                logger.error("Passage embed batch failed @%d: %s", offset, e)
                errors.append({"offset": str(offset), "error": str(e)[:300], "n": str(len(ids))})
                # Retry one-by-one for partial progress.
                for pid, text in zip(ids, texts):
                    try:
                        vecs = emb.embed_batch_with_cache([text], batch_size=1)
                        if vecs:
                            self.add_embeddings_batch([pid], vecs)
                            embedded += 1
                    except Exception as e2:
                        errors.append({"passage_id": pid, "error": str(e2)[:200]})
            if progress_callback and (offset // size) % 5 == 0:
                progress_callback(min(offset + size, total), total, f"embed@{offset}")
                logger.info("Passage embed progress %d/%d", min(offset + size, total), total)

        # Restore isolation mode
        try:
            if old_iso is not None:
                EmbeddingService.ISOLATION_MODE = old_iso  # type: ignore[assignment]
        except Exception:
            pass
        embedded += len(existing)

        # Restore timeout
        try:
            if hasattr(Config, "set"):
                Config.set("embedding.timeout", old_timeout)
            else:
                cfg = getattr(Config, "_data", None) or getattr(Config, "_config", None)
                if isinstance(cfg, dict) and isinstance(cfg.get("embedding"), dict):
                    cfg["embedding"]["timeout"] = old_timeout
        except Exception:
            pass
        return embedded, errors

    # ---------------------------------------------------------------- helpers
    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        if row is None:
            return {}
        if hasattr(row, "keys"):
            d = {k: row[k] for k in row.keys()}
        else:
            # fallback — should not hit with row_factory
            d = dict(row)
        try:
            d["block_ids"] = json.loads(d.get("block_ids_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["block_ids"] = []
        try:
            d["block_ranges"] = json.loads(d.get("block_ranges_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["block_ranges"] = []
        return d

    def _hit_from_row(self, r: Any, *, channel: str, fts: bool = False) -> dict[str, Any]:
        def g(key: str, idx: int, default=None):
            if hasattr(r, "keys"):
                try:
                    return r[key]
                except (KeyError, IndexError):
                    return default
            try:
                return r[idx]
            except Exception:
                return default

        pid = g("id", 0)
        kid = g("knowledge_id", 1)
        text = g("text", 2) or ""
        title = g("title_prefix", 3) or ""
        section = g("section_path", 4) or ""
        family = g("document_family_id", 5) or ""
        vyear = g("version_year", 6)
        sver = g("source_version", 7) or ""
        bids_json = g("block_ids_json", 8) or "[]"
        br_json = g("block_ranges_json", 9) or "[]"
        pidx = g("passage_index", 10) or 0
        try:
            block_ids = json.loads(bids_json) if isinstance(bids_json, str) else (bids_json or [])
        except (json.JSONDecodeError, TypeError):
            block_ids = []
        try:
            block_ranges = json.loads(br_json) if isinstance(br_json, str) else (br_json or [])
        except (json.JSONDecodeError, TypeError):
            block_ranges = []
        dist = g("distance", 15)
        rank = g("rank", 15)
        primary_block = block_ids[0] if block_ids else ""
        meta = {
            "page_id": kid,
            "knowledge_id": kid,
            "passage_id": pid,
            "passage_index": pidx,
            "block_id": primary_block,
            "block_ids": block_ids,
            "block_ranges": block_ranges,
            "document_family_id": family,
            "version_year": vyear,
            "source_version": sver,
            "section_path": section,
            "title": title,
            "retrieval_unit": "passage",
            "family_basis": g("family_basis", 13) or "",
            "family_confidence": g("family_confidence", 14) or 0,
        }
        hit: dict[str, Any] = {
            "id": pid,
            "text": text,
            "metadata": meta,
            "passage_id": pid,
            "knowledge_id": kid,
            "block_id": primary_block,
            "title": title,
            "document_family_id": family,
            "version_year": vyear,
            "match_channels": [channel],
        }
        if not fts and dist is not None:
            from src.models.retrieval import normalize_vector_score
            hit["distance"] = float(dist)
            hit["vector_score"] = normalize_vector_score(float(dist))
        if fts:
            from src.models.retrieval import normalize_fts_score
            fr = float(rank) if rank is not None else 0.0
            hit["fts_rank"] = fr
            hit["keyword_score"] = normalize_fts_score(fr)
            hit["distance"] = 0
        return hit


# Length threshold used in health (aligned with builder short_passage).
SHORT_PASSAGE_THRESHOLD_SAFE = 200


def _pct_from(sorted_vals: list[int], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return float(sorted_vals[idx])

"""SQLite 数据库操作 — 含版本控制与全文索引"""
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.utils.config import Config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT,
    source_type TEXT,
    source_path TEXT,
    file_type TEXT,
    tags TEXT,
    version INTEGER DEFAULT 1,
    file_created_at TEXT DEFAULT '',
    file_modified_at TEXT DEFAULT '',
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_versions (
    id TEXT PRIMARY KEY,
    knowledge_id TEXT REFERENCES knowledge_items(id) ON DELETE CASCADE,
    version INTEGER,
    title TEXT,
    content TEXT,
    tags TEXT,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id TEXT PRIMARY KEY,
    knowledge_id TEXT REFERENCES knowledge_items(id) ON DELETE CASCADE,
    chunk_index INTEGER,
    chunk_text TEXT,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT,
    content TEXT,
    sources TEXT,
    created_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chunks_kid ON knowledge_chunks(knowledge_id);
CREATE INDEX IF NOT EXISTS idx_msgs_cid ON chat_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_versions_kid ON knowledge_versions(knowledge_id, version);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    title, content, tags,
    content=knowledge_items,
    content_rowid=rowid,
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge_items BEGIN
    INSERT INTO knowledge_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge_items BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE ON knowledge_items BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
    INSERT INTO knowledge_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;

-- Chunk 级全文索引（jieba 全模式预分词 + unicode61 按空格分词）
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    fts_segmented,
    knowledge_id UNINDEXED,
    chunk_id UNINDEXED,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    parent_id TEXT REFERENCES categories(id) ON DELETE SET NULL,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_categories (
    knowledge_id TEXT REFERENCES knowledge_items(id) ON DELETE CASCADE,
    category_id TEXT REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (knowledge_id, category_id)
);

-- Wiki 层（LLM 编译产物）
CREATE TABLE IF NOT EXISTS wiki_pages (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT,
    source_ids TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    concept_summary TEXT,
    status TEXT DEFAULT 'active',
    lint_score REAL DEFAULT 1.0,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wiki_links (
    source_page_id TEXT REFERENCES wiki_pages(id) ON DELETE CASCADE,
    target_page_id TEXT REFERENCES wiki_pages(id) ON DELETE CASCADE,
    link_type TEXT DEFAULT 'related',
    weight REAL DEFAULT 1.0,
    PRIMARY KEY (source_page_id, target_page_id)
);

CREATE INDEX IF NOT EXISTS idx_wiki_links_src ON wiki_links(source_page_id);
CREATE INDEX IF NOT EXISTS idx_wiki_links_tgt ON wiki_links(target_page_id);

CREATE TABLE IF NOT EXISTS wiki_ops_log (
    id TEXT PRIMARY KEY,
    op_type TEXT,
    target_id TEXT,
    detail TEXT,
    created_at TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
    title, content, concept_summary,
    content=wiki_pages,
    content_rowid=rowid,
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS wiki_ai AFTER INSERT ON wiki_pages BEGIN
    INSERT INTO wiki_fts(rowid, title, content, concept_summary)
    VALUES (new.rowid, new.title, new.content, new.concept_summary);
END;

CREATE TRIGGER IF NOT EXISTS wiki_ad AFTER DELETE ON wiki_pages BEGIN
    INSERT INTO wiki_fts(wiki_fts, rowid, title, content, concept_summary)
    VALUES ('delete', old.rowid, old.title, old.content, old.concept_summary);
END;

CREATE TRIGGER IF NOT EXISTS wiki_au AFTER UPDATE ON wiki_pages BEGIN
    INSERT INTO wiki_fts(wiki_fts, rowid, title, content, concept_summary)
    VALUES ('delete', old.rowid, old.title, old.content, old.concept_summary);
    INSERT INTO wiki_fts(rowid, title, content, concept_summary)
    VALUES (new.rowid, new.title, new.content, new.concept_summary);
END;

-- 异步任务队列
CREATE TABLE IF NOT EXISTS async_jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    params TEXT DEFAULT '{}',
    progress INTEGER DEFAULT 0,
    progress_message TEXT DEFAULT '',
    result TEXT,
    error_message TEXT DEFAULT '',
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_async_jobs_status ON async_jobs(status);
CREATE INDEX IF NOT EXISTS idx_async_jobs_type ON async_jobs(job_type);
CREATE INDEX IF NOT EXISTS idx_async_jobs_created ON async_jobs(created_at);

-- Wiki 工作流状态转换日志
CREATE TABLE IF NOT EXISTS wiki_workflow (
    id TEXT PRIMARY KEY,
    page_id TEXT REFERENCES wiki_pages(id) ON DELETE CASCADE,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    operator TEXT DEFAULT 'system',
    comment TEXT DEFAULT '',
    created_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wiki_workflow_page ON wiki_workflow(page_id);

-- Wiki 页面版本历史
CREATE TABLE IF NOT EXISTS wiki_page_versions (
    id TEXT PRIMARY KEY,
    page_id TEXT REFERENCES wiki_pages(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    concept_summary TEXT,
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wiki_page_versions_page ON wiki_page_versions(page_id, version);

-- === 知识图谱模块 ===

-- 知识图谱模块
CREATE TABLE IF NOT EXISTS knowledge_graphs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    source_type TEXT DEFAULT 'manual',
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- 图谱中的节点（锚定到 knowledge_items，保留坐标）
CREATE TABLE IF NOT EXISTS knowledge_graph_nodes (
    id TEXT PRIMARY KEY,
    graph_id TEXT REFERENCES knowledge_graphs(id) ON DELETE CASCADE,
    knowledge_id TEXT REFERENCES knowledge_items(id) ON DELETE CASCADE,
    x REAL,
    y REAL,
    is_pinned INTEGER DEFAULT 0,
    UNIQUE(graph_id, knowledge_id)
);

-- 图谱中的关系边
CREATE TABLE IF NOT EXISTS knowledge_graph_relations (
    id TEXT PRIMARY KEY,
    graph_id TEXT REFERENCES knowledge_graphs(id) ON DELETE CASCADE,
    source_knowledge_id TEXT NOT NULL,
    target_knowledge_id TEXT NOT NULL,
    relation_type TEXT DEFAULT 'related',
    description TEXT,
    weight REAL DEFAULT 1.0
);

CREATE INDEX IF NOT EXISTS idx_graph_rel_src ON knowledge_graph_relations(graph_id, source_knowledge_id);
CREATE INDEX IF NOT EXISTS idx_graph_rel_tgt ON knowledge_graph_relations(graph_id, target_knowledge_id);
"""


class Database:
    _instance = None
    _conn: Optional[sqlite3.Connection] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def connect(cls, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Config.get_db_path()
        cls._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cls._conn.row_factory = sqlite3.Row
        cls._conn.execute("PRAGMA foreign_keys = ON")
        cls._conn.execute("PRAGMA journal_mode = WAL")
        cls._conn.executescript(_SCHEMA)
        cls._migrate()
        cls._conn.commit()

    @classmethod
    def _migrate(cls):
        """检查并补齐旧数据库缺失的列"""
        cols = {row[1] for row in cls._conn.execute("PRAGMA table_info(knowledge_items)").fetchall()}
        if "version" not in cols:
            cls._conn.execute("ALTER TABLE knowledge_items ADD COLUMN version INTEGER DEFAULT 1")
        if "file_size" not in cols:
            cls._conn.execute("ALTER TABLE knowledge_items ADD COLUMN file_size INTEGER DEFAULT 0")
        if "content_hash" not in cols:
            cls._conn.execute("ALTER TABLE knowledge_items ADD COLUMN content_hash TEXT DEFAULT ''")
        if "quality" not in cols:
            cls._conn.execute("ALTER TABLE knowledge_items ADD COLUMN quality TEXT DEFAULT ''")
        if "file_created_at" not in cols:
            cls._conn.execute("ALTER TABLE knowledge_items ADD COLUMN file_created_at TEXT DEFAULT ''")
        if "file_modified_at" not in cols:
            cls._conn.execute("ALTER TABLE knowledge_items ADD COLUMN file_modified_at TEXT DEFAULT ''")

        # 去重索引
        cls._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_hash ON knowledge_items(content_hash)"
        )

        # chunk_fts 重建：检测旧 schema（含 content=knowledge_chunks 或缺少 chunk_id）
        import logging as _logging
        chunk_fts_sql = cls._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='chunk_fts'"
        ).fetchone()
        needs_rebuild = False
        if chunk_fts_sql:
            sql = chunk_fts_sql[0] or ''
            if 'content=knowledge_chunks' in sql:
                needs_rebuild = True
            elif 'chunk_id UNINDEXED' not in sql:
                needs_rebuild = True
        if needs_rebuild:
            cls._conn.execute("DROP TABLE IF EXISTS chunk_fts")
            cls._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5("
                "fts_segmented, knowledge_id UNINDEXED, chunk_id UNINDEXED, tokenize='unicode61')"
            )
            cls._conn.commit()
            _logging.getLogger(__name__).info("chunk_fts schema migrated, reindex needed")

    @classmethod
    def get_conn(cls) -> sqlite3.Connection:
        if cls._conn is None:
            cls.connect()
        return cls._conn

    @classmethod
    def close(cls):
        if cls._conn:
            cls._conn.close()
            cls._conn = None

    # ---- Knowledge Items ----

    @classmethod
    def insert_knowledge(cls, item: dict) -> str:
        conn = cls.get_conn()
        conn.execute(
            """INSERT INTO knowledge_items
               (id, title, content, source_type, source_path, file_type, file_size, content_hash, file_created_at, file_modified_at, tags, version, created_at, updated_at)
               VALUES (:id, :title, :content, :source_type, :source_path, :file_type, :file_size, :content_hash, :file_created_at, :file_modified_at, :tags, :version, :created_at, :updated_at)""",
            item,
        )
        conn.commit()
        return item["id"]

    @classmethod
    def get_knowledge(cls, item_id: str) -> Optional[dict]:
        row = cls.get_conn().execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    @classmethod
    def get_knowledge_by_hash(cls, content_hash: str) -> Optional[dict]:
        """按内容哈希查重，返回第一条匹配记录"""
        row = cls.get_conn().execute(
            "SELECT * FROM knowledge_items WHERE content_hash = ? LIMIT 1", (content_hash,)
        ).fetchone()
        return dict(row) if row else None

    @classmethod
    def get_knowledge_batch(cls, ids: list[str]) -> dict[str, dict]:
        """批量查询知识条目，返回 {id: row_dict}"""
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = cls.get_conn().execute(
            f"SELECT * FROM knowledge_items WHERE id IN ({placeholders})", ids
        ).fetchall()
        return {row["id"]: dict(row) for row in rows}

    @classmethod
    def list_knowledge(cls, tag: str | None = None, file_type: str | None = None,
                       quality: str | None = None,
                       sort_by: str = "updated_at", sort_order: str = "DESC",
                       limit: int = 100, offset: int = 0) -> list[dict]:
        conn = cls.get_conn()
        conditions = []
        params = []
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if file_type:
            conditions.append("file_type = ?")
            params.append(file_type)
        if quality is not None:
            conditions.append("quality = ?")
            params.append(quality)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        valid_sorts = {"updated_at", "created_at", "title", "version"}
        sort_by = sort_by if sort_by in valid_sorts else "updated_at"
        sort_order = "DESC" if sort_order.upper() == "DESC" else "ASC"
        rows = conn.execute(
            f"SELECT * FROM knowledge_items{where} ORDER BY {sort_by} {sort_order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def search_knowledge(cls, query: str, limit: int = 20, offset: int = 0) -> list[dict]:
        from src.utils.chinese_tokenizer import sanitize_fts_query
        conn = cls.get_conn()
        try:
            safe_query = sanitize_fts_query(query)
            if safe_query:
                fts_rows = conn.execute(
                    """SELECT ki.*, rank as fts_rank FROM knowledge_fts kf
                       JOIN knowledge_items ki ON ki.rowid = kf.rowid
                       WHERE knowledge_fts MATCH ? ORDER BY fts_rank LIMIT ? OFFSET ?""",
                    (safe_query, limit, offset),
                ).fetchall()
                if fts_rows:
                    return [dict(r) for r in fts_rows]
        except Exception:
            pass
        rows = conn.execute(
            "SELECT * FROM knowledge_items WHERE title LIKE ? OR content LIKE ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (f"%{query}%", f"%{query}%", limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def get_all_classified_ids(cls) -> set[str]:
        """返回所有已分类条目的 ID 集合"""
        rows = cls.get_conn().execute(
            "SELECT DISTINCT knowledge_id FROM knowledge_categories"
        ).fetchall()
        return {row[0] for row in rows}

    @classmethod
    def update_knowledge(cls, item_id: str, **fields):
        if not fields:
            return
        allowed = {"title", "content", "source_type", "source_path", "file_type", "file_size", "file_created_at", "file_modified_at", "tags", "quality"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Invalid fields: {invalid}")
        old = cls.get_knowledge(item_id)
        if old and old.get("content") != fields.get("content"):
            cls._save_version(item_id, old)
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [datetime.now().isoformat(), item_id]
        cls.get_conn().execute(
            f"UPDATE knowledge_items SET {sets}, version = version + 1, updated_at = ? WHERE id = ?",
            values,
        )
        cls.get_conn().commit()

    @classmethod
    def delete_knowledge(cls, item_id: str):
        from src.services.vectorstore import VectorStore
        VectorStore().delete_by_knowledge(item_id)
        conn = cls.get_conn()
        cls.delete_chunks_fts(item_id)
        conn.execute("DELETE FROM knowledge_chunks WHERE knowledge_id = ?", (item_id,))
        conn.execute("DELETE FROM knowledge_versions WHERE knowledge_id = ?", (item_id,))
        conn.execute("DELETE FROM knowledge_items WHERE id = ?", (item_id,))
        conn.commit()

    @classmethod
    def find_duplicates(cls) -> list[list[dict]]:
        """查找重复条目组：按 source_path + file_size + file_created_at + file_modified_at 四项完全一致判定重复。
        每组按 created_at 降序（最新在前）。"""
        conn = cls.get_conn()
        rows = conn.execute(
            "SELECT id, title, source_path, file_size, file_created_at, file_modified_at, created_at FROM knowledge_items"
        ).fetchall()

        groups = {}
        for row in rows:
            src = (row["source_path"] or "").strip()
            size = row["file_size"] or 0
            fcat = (row["file_created_at"] or "").strip()
            fmat = (row["file_modified_at"] or "").strip()
            if src and size > 0:
                key = (src, size, fcat, fmat)
                groups.setdefault(key, []).append(dict(row))

        result = []
        for g in groups.values():
            if len(g) > 1:
                g.sort(key=lambda x: x.get("created_at", ""), reverse=True)
                result.append(g)
        return result

    @classmethod
    def count_knowledge(cls, tag: str | None = None) -> int:
        if tag:
            row = cls.get_conn().execute(
                "SELECT COUNT(*) as cnt FROM knowledge_items WHERE tags LIKE ?",
                (f'%"{tag}"%',),
            ).fetchone()
        else:
            row = cls.get_conn().execute("SELECT COUNT(*) as cnt FROM knowledge_items").fetchone()
        return row["cnt"]

    @classmethod
    def get_stats(cls) -> dict:
        """返回知识库统计汇总：文件数、存储占用、类型分布、分类覆盖"""
        conn = cls.get_conn()
        total_files = conn.execute("SELECT COUNT(*) as cnt FROM knowledge_items").fetchone()["cnt"]
        total_size = conn.execute(
            "SELECT COALESCE(SUM(file_size), 0) as sz FROM knowledge_items"
        ).fetchone()["sz"]

        # 文件类型分布
        type_rows = conn.execute(
            "SELECT file_type, COUNT(*) as cnt FROM knowledge_items GROUP BY file_type ORDER BY cnt DESC"
        ).fetchall()
        file_type_dist = {r["file_type"] or "other": r["cnt"] for r in type_rows}

        # 分类覆盖数（有至少一个条目的分类）
        cat_count = 0
        try:
            cat_rows = conn.execute(
                "SELECT DISTINCT category_id FROM knowledge_categories"
            ).fetchall()
            cat_count = len(cat_rows)
        except Exception:
            pass

        return {
            "total_files": total_files,
            "total_size": total_size,
            "file_type_dist": file_type_dist,
            "category_coverage": cat_count,
        }

    # ---- Version Control ----

    @classmethod
    def _save_version(cls, knowledge_id: str, snapshot: dict):
        version = snapshot.get("version", 1)
        cls.get_conn().execute(
            """INSERT INTO knowledge_versions (id, knowledge_id, version, title, content, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), knowledge_id, version, snapshot["title"],
             snapshot.get("content", ""), snapshot.get("tags", "[]"), datetime.now().isoformat()),
        )
        cls.get_conn().commit()

    @classmethod
    def list_versions(cls, knowledge_id: str) -> list[dict]:
        rows = cls.get_conn().execute(
            "SELECT * FROM knowledge_versions WHERE knowledge_id = ? ORDER BY version DESC",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def get_version(cls, knowledge_id: str, version: int) -> Optional[dict]:
        row = cls.get_conn().execute(
            "SELECT * FROM knowledge_versions WHERE knowledge_id = ? AND version = ?",
            (knowledge_id, version),
        ).fetchone()
        return dict(row) if row else None

    @classmethod
    def restore_version(cls, knowledge_id: str, version: int):
        ver = cls.get_version(knowledge_id, version)
        if not ver:
            raise ValueError(f"版本 {version} 不存在")
        old = cls.get_knowledge(knowledge_id)
        if old:
            cls._save_version(knowledge_id, old)
        cls.get_conn().execute(
            "UPDATE knowledge_items SET title = ?, content = ?, tags = ?, version = version + 1, updated_at = ? WHERE id = ?",
            (ver["title"], ver["content"], ver["tags"], datetime.now().isoformat(), knowledge_id),
        )
        cls.get_conn().commit()

    # ---- Knowledge Chunks ----

    @classmethod
    def insert_chunks(cls, chunks: list[dict]):
        conn = cls.get_conn()
        conn.executemany(
            """INSERT INTO knowledge_chunks (id, knowledge_id, chunk_index, chunk_text, created_at)
               VALUES (:id, :knowledge_id, :chunk_index, :chunk_text, :created_at)""",
            chunks,
        )
        conn.commit()

    @classmethod
    def get_chunks_by_knowledge(cls, knowledge_id: str) -> list[dict]:
        rows = cls.get_conn().execute(
            "SELECT * FROM knowledge_chunks WHERE knowledge_id = ? ORDER BY chunk_index",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def get_chunk(cls, chunk_id: str) -> Optional[dict]:
        row = cls.get_conn().execute("SELECT * FROM knowledge_chunks WHERE id = ?", (chunk_id,)).fetchone()
        return dict(row) if row else None

    # ---- Chunk FTS (jieba 分词) ----

    @classmethod
    def insert_chunks_fts(cls, chunks: list[dict]):
        """将 chunk 文本用 jieba 全模式分词后写入 chunk_fts（独立表）"""
        from src.utils.chinese_tokenizer import tokenize_chinese_full
        conn = cls.get_conn()
        for c in chunks:
            segmented = tokenize_chinese_full(c["chunk_text"])
            conn.execute(
                "INSERT INTO chunk_fts(fts_segmented, knowledge_id, chunk_id) VALUES (?, ?, ?)",
                (segmented, c["knowledge_id"], c["id"]),
            )
        conn.commit()

    @classmethod
    def delete_chunks_fts(cls, knowledge_id: str):
        """删除指定知识的 chunk FTS 记录"""
        cls.get_conn().execute(
            "DELETE FROM chunk_fts WHERE knowledge_id = ?", (knowledge_id,)
        )
        cls.get_conn().commit()

    @classmethod
    def search_chunks_fts(cls, query: str, limit: int = 20) -> list[dict]:
        """使用 jieba 全模式分词后的 chunk 级 FTS 搜索"""
        from src.utils.chinese_tokenizer import tokenize_chinese_full, sanitize_fts_query
        tokenized_query = tokenize_chinese_full(query)
        if not tokenized_query.strip():
            return []
        safe_query = sanitize_fts_query(tokenized_query, is_tokenized=True)
        if not safe_query:
            return []
        conn = cls.get_conn()
        try:
            rows = conn.execute(
                """SELECT cf.chunk_id, cf.knowledge_id, rank as fts_rank
                   FROM chunk_fts cf
                   WHERE chunk_fts MATCH ?
                   ORDER BY fts_rank LIMIT ?""",
                (safe_query, limit),
            ).fetchall()
            results = []
            for r in rows:
                chunk = conn.execute(
                    "SELECT id, knowledge_id, chunk_index, chunk_text FROM knowledge_chunks WHERE id = ?",
                    (r["chunk_id"],),
                ).fetchone()
                if chunk:
                    results.append(dict(chunk) | {"fts_rank": r["fts_rank"]})
            return results
        except Exception:
            return []

    # ---- Conversations ----

    @classmethod
    def insert_conversation(cls, conv: dict) -> str:
        cls.get_conn().execute(
            "INSERT INTO conversations (id, title, created_at) VALUES (:id, :title, :created_at)",
            conv,
        )
        cls.get_conn().commit()
        return conv["id"]

    @classmethod
    def list_conversations(cls, limit: int = 50) -> list[dict]:
        rows = cls.get_conn().execute(
            "SELECT * FROM conversations ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def delete_conversation(cls, conv_id: str):
        conn = cls.get_conn()
        conn.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        conn.commit()

    # ---- Chat Messages ----

    @classmethod
    def insert_message(cls, msg: dict) -> str:
        cls.get_conn().execute(
            """INSERT INTO chat_messages (id, conversation_id, role, content, sources, created_at)
               VALUES (:id, :conversation_id, :role, :content, :sources, :created_at)""",
            msg,
        )
        cls.get_conn().commit()
        return msg["id"]

    @classmethod
    def get_messages(cls, conversation_id: str) -> list[dict]:
        rows = cls.get_conn().execute(
            "SELECT * FROM chat_messages WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Tags ----

    @classmethod
    def get_all_tags(cls) -> list[str]:
        rows = cls.get_conn().execute("SELECT tags FROM knowledge_items WHERE tags IS NOT NULL").fetchall()
        tags_set = set()
        for row in rows:
            try:
                tags = json.loads(row["tags"])
                tags_set.update(tags)
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(tags_set)

    @classmethod
    def get_all_file_types(cls) -> list[str]:
        """返回知识库中所有已使用的文件类型"""
        rows = cls.get_conn().execute(
            "SELECT DISTINCT file_type FROM knowledge_items WHERE file_type IS NOT NULL AND file_type != '' ORDER BY file_type"
        ).fetchall()
        return [row["file_type"] for row in rows]

    # ---- Categories ----

    @classmethod
    def insert_category(cls, cat_id: str, name: str, description: str = "", parent_id: str | None = None) -> str:
        cls.get_conn().execute(
            "INSERT INTO categories (id, name, description, parent_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (cat_id, name, description, parent_id, datetime.now().isoformat()),
        )
        cls.get_conn().commit()
        return cat_id

    @classmethod
    def get_all_categories(cls) -> list[dict]:
        rows = cls.get_conn().execute("SELECT * FROM categories ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def delete_category(cls, cat_id: str):
        conn = cls.get_conn()
        conn.execute("DELETE FROM knowledge_categories WHERE category_id = ?", (cat_id,))
        conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        conn.commit()

    @classmethod
    def clear_categories(cls, keep_dynamic=False):
        conn = cls.get_conn()
        conn.execute("DELETE FROM knowledge_categories")
        if keep_dynamic:
            # 只删除预设分类（名称以 schema code 开头的），保留动态分类
            from src.data.classification_schema import get_all_codes
            schema_codes = get_all_codes()
            for code in schema_codes:
                conn.execute("DELETE FROM categories WHERE name LIKE ?", (f"{code} %",))
                conn.execute("DELETE FROM categories WHERE name = ?", (f"{code}",))
        else:
            conn.execute("DELETE FROM categories")
        conn.commit()

    @classmethod
    def assign_category(cls, knowledge_id: str, category_id: str):
        cls.get_conn().execute(
            "INSERT OR IGNORE INTO knowledge_categories (knowledge_id, category_id) VALUES (?, ?)",
            (knowledge_id, category_id),
        )
        cls.get_conn().commit()

    @classmethod
    def get_knowledge_by_category(cls, category_id: str) -> list[dict]:
        rows = cls.get_conn().execute(
            """SELECT ki.* FROM knowledge_items ki
               JOIN knowledge_categories kc ON kc.knowledge_id = ki.id
               WHERE kc.category_id = ? ORDER BY ki.title""",
            (category_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def get_categories_for_knowledge(cls, knowledge_id: str) -> list[dict]:
        rows = cls.get_conn().execute(
            """SELECT c.* FROM categories c
               JOIN knowledge_categories kc ON kc.category_id = c.id
               WHERE kc.knowledge_id = ?""",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Wiki Pages ----

    @classmethod
    def insert_wiki_page(cls, page: dict) -> str:
        conn = cls.get_conn()
        conn.execute(
            """INSERT INTO wiki_pages
               (id, title, content, source_ids, tags, concept_summary, status, lint_score, created_at, updated_at)
               VALUES (:id, :title, :content, :source_ids, :tags, :concept_summary, :status, :lint_score, :created_at, :updated_at)""",
            page,
        )
        conn.commit()
        return page["id"]

    @classmethod
    def get_wiki_page(cls, page_id: str) -> Optional[dict]:
        row = cls.get_conn().execute("SELECT * FROM wiki_pages WHERE id = ?", (page_id,)).fetchone()
        return dict(row) if row else None

    @classmethod
    def get_wiki_page_by_title(cls, title: str) -> Optional[dict]:
        row = cls.get_conn().execute("SELECT * FROM wiki_pages WHERE title = ?", (title,)).fetchone()
        return dict(row) if row else None

    @classmethod
    def update_wiki_page(cls, page_id: str, **fields):
        if not fields:
            return
        allowed = {"title", "content", "source_ids", "tags", "concept_summary", "status", "lint_score"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Invalid fields: {invalid}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [datetime.now().isoformat(), page_id]
        cls.get_conn().execute(
            f"UPDATE wiki_pages SET {sets}, updated_at = ? WHERE id = ?",
            values,
        )
        cls.get_conn().commit()

    @classmethod
    def delete_wiki_page(cls, page_id: str):
        conn = cls.get_conn()
        conn.execute("DELETE FROM wiki_links WHERE source_page_id = ? OR target_page_id = ?", (page_id, page_id))
        conn.execute("DELETE FROM wiki_pages WHERE id = ?", (page_id,))
        conn.commit()

    @classmethod
    def list_wiki_pages(cls, status: str | None = None, search: str | None = None,
                        sort_by: str = "updated_at", sort_order: str = "DESC",
                        limit: int = 100, offset: int = 0) -> list[dict]:
        conn = cls.get_conn()
        conditions = []
        params = []
        if status:
            # 向后兼容：active -> published
            if status == "active":
                status = "published"
            conditions.append("status = ?")
            params.append(status)
        if search:
            conditions.append("title LIKE ?")
            params.append(f"%{search}%")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        valid_sorts = {"updated_at", "created_at", "title", "lint_score"}
        sort_by = sort_by if sort_by in valid_sorts else "updated_at"
        sort_order = "DESC" if sort_order.upper() == "DESC" else "ASC"
        rows = conn.execute(
            f"SELECT * FROM wiki_pages{where} ORDER BY {sort_by} {sort_order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def count_wiki_pages(cls, status: str | None = None) -> int:
        if status:
            row = cls.get_conn().execute("SELECT COUNT(*) as cnt FROM wiki_pages WHERE status = ?", (status,)).fetchone()
        else:
            row = cls.get_conn().execute("SELECT COUNT(*) as cnt FROM wiki_pages").fetchone()
        return row["cnt"]

    @classmethod
    def search_wiki_fts(cls, query: str, limit: int = 10) -> list[dict]:
        from src.utils.chinese_tokenizer import sanitize_fts_query
        try:
            safe_query = sanitize_fts_query(query)
            if not safe_query:
                return []
            rows = cls.get_conn().execute(
                """SELECT wp.*, rank as fts_rank FROM wiki_fts wf
                   JOIN wiki_pages wp ON wp.rowid = wf.rowid
                   WHERE wiki_fts MATCH ? AND wp.status = 'published'
                   ORDER BY fts_rank LIMIT ?""",
                (safe_query, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    # ---- Wiki Links ----

    @classmethod
    def add_wiki_link(cls, source_page_id: str, target_page_id: str,
                      link_type: str = "related", weight: float = 1.0):
        cls.get_conn().execute(
            "INSERT OR REPLACE INTO wiki_links (source_page_id, target_page_id, link_type, weight) VALUES (?, ?, ?, ?)",
            (source_page_id, target_page_id, link_type, weight),
        )
        cls.get_conn().commit()

    @classmethod
    def remove_wiki_link(cls, source_page_id: str, target_page_id: str):
        cls.get_conn().execute(
            "DELETE FROM wiki_links WHERE source_page_id = ? AND target_page_id = ?",
            (source_page_id, target_page_id),
        )
        cls.get_conn().commit()

    @classmethod
    def get_links_for_page(cls, page_id: str) -> list[dict]:
        rows = cls.get_conn().execute(
            """SELECT wl.*, wp.title as target_title FROM wiki_links wl
               JOIN wiki_pages wp ON wp.id = wl.target_page_id
               WHERE wl.source_page_id = ?""",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def get_backlinks(cls, page_id: str) -> list[dict]:
        rows = cls.get_conn().execute(
            """SELECT wl.*, wp.title as source_title FROM wiki_links wl
               JOIN wiki_pages wp ON wp.id = wl.source_page_id
               WHERE wl.target_page_id = ?""",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def get_all_wiki_links(cls) -> list[dict]:
        rows = cls.get_conn().execute(
            """SELECT wl.*, sp.title as source_title, tp.title as target_title
               FROM wiki_links wl
               JOIN wiki_pages sp ON sp.id = wl.source_page_id
               JOIN wiki_pages tp ON tp.id = wl.target_page_id""",
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Wiki Ops Log ----

    @classmethod
    def insert_wiki_op(cls, op_type: str, target_id: str, detail: dict | None = None) -> str:
        op_id = str(uuid.uuid4())
        cls.get_conn().execute(
            "INSERT INTO wiki_ops_log (id, op_type, target_id, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (op_id, op_type, target_id, json.dumps(detail or {}, ensure_ascii=False), datetime.now().isoformat()),
        )
        cls.get_conn().commit()
        return op_id

    @classmethod
    def list_wiki_ops(cls, limit: int = 50) -> list[dict]:
        rows = cls.get_conn().execute(
            "SELECT * FROM wiki_ops_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Async Jobs ----

    @classmethod
    def create_job(cls, job_type: str, params: dict | None = None, priority: int = 1, max_retries: int = 3) -> str:
        """创建新任务"""
        import uuid as _uuid
        job_id = str(_uuid.uuid4())
        now = datetime.now().isoformat()
        conn = cls.get_conn()
        conn.execute(
            """INSERT INTO async_jobs
               (id, job_type, status, params, priority, max_retries, created_at)
               VALUES (?, ?, 'pending', ?, ?, ?, ?)""",
            (job_id, job_type, json.dumps(params or {}), priority, max_retries, now),
        )
        conn.commit()
        return job_id

    @classmethod
    def get_job(cls, job_id: str) -> Optional[dict]:
        """获取任务详情"""
        row = cls.get_conn().execute("SELECT * FROM async_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["params"] = json.loads(result.get("params", "{}"))
        result["result"] = json.loads(result["result"]) if result.get("result") else None
        return result

    @classmethod
    def list_jobs(cls, status: str | None = None, job_type: str | None = None,
                  limit: int = 50, offset: int = 0) -> list[dict]:
        """列出任务"""
        conn = cls.get_conn()
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if job_type:
            conditions.append("job_type = ?")
            params.append(job_type)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM async_jobs{where} ORDER BY priority DESC, created_at ASC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def update_job_progress(cls, job_id: str, progress: int, message: str = ""):
        """更新任务进度"""
        cls.get_conn().execute(
            "UPDATE async_jobs SET progress = ?, progress_message = ? WHERE id = ?",
            (progress, message, job_id),
        )
        cls.get_conn().commit()

    @classmethod
    def update_job_status(cls, job_id: str, status: str, result: dict | None = None, error: str = ""):
        """更新任务状态"""
        now = datetime.now().isoformat()
        conn = cls.get_conn()
        if status == "running" and not cls.get_job(job_id).get("started_at"):
            conn.execute(
                "UPDATE async_jobs SET status = ?, started_at = ?, progress = ? WHERE id = ?",
                (status, now, 0, job_id),
            )
        elif status in ("completed", "failed", "cancelled"):
            conn.execute(
                "UPDATE async_jobs SET status = ?, completed_at = ?, result = ?, error_message = ? WHERE id = ?",
                (status, now, json.dumps(result) if result else None, error, job_id),
            )
        else:
            conn.execute("UPDATE async_jobs SET status = ? WHERE id = ?", (status, job_id))
        conn.commit()

    @classmethod
    def claim_next_pending_job(cls) -> Optional[dict]:
        """认领下一个待处理任务（原子操作）"""
        conn = cls.get_conn()
        row = conn.execute(
            """UPDATE async_jobs
               SET status = 'running', started_at = ?
               WHERE id = (
                   SELECT id FROM async_jobs
                   WHERE status = 'pending'
                   ORDER BY priority DESC, created_at ASC
                   LIMIT 1
               )
               RETURNING *""",
            (datetime.now().isoformat(),),
        ).fetchone()
        conn.commit()
        return dict(row) if row else None

    @classmethod
    def cancel_job(cls, job_id: str) -> bool:
        """取消任务"""
        job = cls.get_job(job_id)
        if not job:
            return False
        if job["status"] in ("pending", "running"):
            cls.update_job_status(job_id, "cancelled")
            return True
        return False

    @classmethod
    def delete_job(cls, job_id: str) -> bool:
        """删除已完成/失败的任务"""
        job = cls.get_job(job_id)
        if not job or job["status"] not in ("completed", "failed", "cancelled"):
            return False
        cls.get_conn().execute("DELETE FROM async_jobs WHERE id = ?", (job_id,))
        cls.get_conn().commit()
        return True

    @classmethod
    def cleanup_old_jobs(cls, retention_days: int = 7):
        """清理超过指定天数的已完成/失败任务"""
        conn = cls.get_conn()
        conn.execute(
            """DELETE FROM async_jobs
               WHERE status IN ('completed', 'failed', 'cancelled')
               AND completed_at < datetime('now', '-' || ? || ' days')""",
            (retention_days,),
        )
        conn.commit()

    @classmethod
    def get_job_stats(cls) -> dict:
        """获取任务统计"""
        conn = cls.get_conn()
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM async_jobs GROUP BY status"
        ).fetchall()
        return {row["status"]: row["count"] for row in rows}

    # ---- Wiki Workflow ----

    @classmethod
    def insert_workflow(cls, page_id: str, from_status: str, to_status: str,
                        operator: str = "system", comment: str = "") -> str:
        """记录工作流状态转换"""
        import uuid as _uuid
        wf_id = str(_uuid.uuid4())
        now = datetime.now().isoformat()
        cls.get_conn().execute(
            """INSERT INTO wiki_workflow (id, page_id, from_status, to_status, operator, comment, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (wf_id, page_id, from_status, to_status, operator, comment, now),
        )
        cls.get_conn().commit()
        return wf_id

    @classmethod
    def get_workflow_history(cls, page_id: str) -> list[dict]:
        """获取页面的工作流历史"""
        rows = cls.get_conn().execute(
            "SELECT * FROM wiki_workflow WHERE page_id = ? ORDER BY created_at DESC",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Wiki Page Versions ----

    @classmethod
    def save_wiki_version(cls, page_id: str, page_data: dict) -> str:
        """保存 Wiki 页面版本快照"""
        import uuid as _uuid
        version_id = str(_uuid.uuid4())
        now = datetime.now().isoformat()
        # 获取当前最大版本号
        row = cls.get_conn().execute(
            "SELECT MAX(version) as max_ver FROM wiki_page_versions WHERE page_id = ?",
            (page_id,),
        ).fetchone()
        next_version = (row["max_ver"] or 0) + 1
        cls.get_conn().execute(
            """INSERT INTO wiki_page_versions
               (id, page_id, version, title, content, concept_summary, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, page_id, next_version, page_data.get("title", ""),
             page_data.get("content", ""), page_data.get("concept_summary", ""),
             page_data.get("tags", "[]"), now),
        )
        cls.get_conn().commit()
        return version_id

    @classmethod
    def list_wiki_versions(cls, page_id: str) -> list[dict]:
        """列出页面所有版本"""
        rows = cls.get_conn().execute(
            "SELECT * FROM wiki_page_versions WHERE page_id = ? ORDER BY version DESC",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def get_wiki_version(cls, page_id: str, version: int) -> Optional[dict]:
        """获取指定版本"""
        row = cls.get_conn().execute(
            "SELECT * FROM wiki_page_versions WHERE page_id = ? AND version = ?",
            (page_id, version),
        ).fetchone()
        return dict(row) if row else None

    @classmethod
    def get_latest_wiki_version(cls, page_id: str) -> Optional[dict]:
        """获取最新版本"""
        row = cls.get_conn().execute(
            "SELECT * FROM wiki_page_versions WHERE page_id = ? ORDER BY version DESC LIMIT 1",
            (page_id,),
        ).fetchone()
        return dict(row) if row else None

    # ---- Knowledge Graphs ----

    @classmethod
    def insert_graph(cls, name: str, description: str = "", source_type: str = "manual") -> str:
        graph_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        cls.get_conn().execute(
            "INSERT INTO knowledge_graphs (id, name, description, source_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (graph_id, name, description, source_type, now, now),
        )
        cls.get_conn().commit()
        return graph_id

    @classmethod
    def get_graph(cls, graph_id: str) -> Optional[dict]:
        row = cls.get_conn().execute("SELECT * FROM knowledge_graphs WHERE id = ?", (graph_id,)).fetchone()
        return dict(row) if row else None

    @classmethod
    def list_graphs(cls, source_type: str | None = None) -> list[dict]:
        conn = cls.get_conn()
        if source_type:
            rows = conn.execute(
                "SELECT * FROM knowledge_graphs WHERE source_type = ? ORDER BY updated_at DESC",
                (source_type,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM knowledge_graphs ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def delete_graph(cls, graph_id: str):
        # 级联删除由外键约束自动处理
        conn = cls.get_conn()
        conn.execute("DELETE FROM knowledge_graphs WHERE id = ?", (graph_id,))
        conn.commit()

    @classmethod
    def update_graph(cls, graph_id: str, **fields):
        allowed = {"name", "description"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Invalid fields: {invalid}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [datetime.now().isoformat(), graph_id]
        cls.get_conn().execute(
            f"UPDATE knowledge_graphs SET {sets}, updated_at = ? WHERE id = ?",
            values,
        )
        cls.get_conn().commit()

    # ---- Knowledge Graph Nodes ----

    @classmethod
    def insert_graph_nodes(cls, graph_id: str, knowledge_ids: list[str]):
        conn = cls.get_conn()
        for knowledge_id in knowledge_ids:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_graph_nodes (id, graph_id, knowledge_id, x, y, is_pinned) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), graph_id, knowledge_id, 0, 0, 0),
            )
        conn.commit()

    @classmethod
    def get_graph_nodes(cls, graph_id: str) -> list[dict]:
        rows = cls.get_conn().execute(
            """SELECT n.*, ki.title as knowledge_title, ki.file_type, ki.tags
               FROM knowledge_graph_nodes n
               JOIN knowledge_items ki ON ki.id = n.knowledge_id
               WHERE n.graph_id = ?""",
            (graph_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def update_node_position(cls, node_id: str, x: float, y: float):
        cls.get_conn().execute(
            "UPDATE knowledge_graph_nodes SET x = ?, y = ? WHERE id = ?",
            (x, y, node_id),
        )
        cls.get_conn().commit()

    @classmethod
    def delete_graph_nodes(cls, graph_id: str, knowledge_ids: list[str]):
        if not knowledge_ids:
            return
        placeholders = ",".join("?" for _ in knowledge_ids)
        cls.get_conn().execute(
            f"DELETE FROM knowledge_graph_nodes WHERE graph_id = ? AND knowledge_id IN ({placeholders})",
            (graph_id, *knowledge_ids),
        )
        cls.get_conn().commit()

    # ---- Knowledge Graph Relations ----

    @classmethod
    def insert_graph_relations(cls, graph_id: str, relations: list[dict]):
        conn = cls.get_conn()
        for rel in relations:
            conn.execute(
                """INSERT OR REPLACE INTO knowledge_graph_relations
                   (id, graph_id, source_knowledge_id, target_knowledge_id, relation_type, description, weight)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), graph_id,
                 rel["source_knowledge_id"], rel["target_knowledge_id"],
                 rel.get("relation_type", "related"),
                 rel.get("description", ""),
                 rel.get("weight", 1.0)),
            )
        conn.commit()

    @classmethod
    def get_graph_relations(cls, graph_id: str) -> list[dict]:
        rows = cls.get_conn().execute(
            "SELECT * FROM knowledge_graph_relations WHERE graph_id = ?",
            (graph_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def delete_graph_relations(cls, graph_id: str):
        cls.get_conn().execute(
            "DELETE FROM knowledge_graph_relations WHERE graph_id = ?", (graph_id,)
        )
        cls.get_conn().commit()

    @classmethod
    def get_graph_for_knowledge(cls, knowledge_id: str) -> list[dict]:
        """获取包含指定知识的所有图谱"""
        rows = cls.get_conn().execute(
            """SELECT g.* FROM knowledge_graphs g
               JOIN knowledge_graph_nodes n ON n.graph_id = g.id
               WHERE n.knowledge_id = ?""",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]

"""Legacy runtime schema apply — compatibility only (WP5).

Production startup must use Alembic + Database.open_runtime().
This module exists for:
  - test fixtures (Database legacy constructor)
  - historical reference / detector baselines

Do not import from production create_container / open_runtime paths.
"""
from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

LEGACY_SCHEMA_SQL = """

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
    updated_at TIMESTAMP,
    deleted_at TEXT DEFAULT NULL
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
    source_graph TEXT DEFAULT '{"nodes":[],"edges":[]}',
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

CREATE VIRTUAL TABLE IF NOT EXISTS block_fts USING fts5(
    fts_segmented,
    page_id UNINDEXED,
    block_id UNINDEXED,
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
    complex_anomaly TEXT DEFAULT '',
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

-- === Canonical Wiki v2 投影层（Phase 2）===
-- canonical filesystem (wiki/*.md + claims/*.yaml) 的 SQLite 投影,
-- 由 WikiProjection 服务消费 outbox 维护。旧 wiki_pages/wiki_links/
-- wiki_ops_log/wiki_fts 保留供兼容读取,不删除。
CREATE TABLE IF NOT EXISTS wiki_pages_v2 (
    page_id TEXT PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    page_type TEXT NOT NULL,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    claim_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_v2_type ON wiki_pages_v2(page_type);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_v2_status ON wiki_pages_v2(status);

CREATE TABLE IF NOT EXISTS wiki_claims (
    claim_id TEXT PRIMARY KEY,
    statement TEXT NOT NULL,
    normalized_statement TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    claim_scope TEXT,
    valid_from TEXT,
    valid_to TEXT,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wiki_claims_status ON wiki_claims(status);
CREATE INDEX IF NOT EXISTS idx_wiki_claims_normalized ON wiki_claims(normalized_statement);

CREATE TABLE IF NOT EXISTS wiki_claim_evidence (
    evidence_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    stance TEXT NOT NULL,
    knowledge_id TEXT NOT NULL,
    block_id TEXT,
    location_json TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    excerpt_hash TEXT,
    observed_at TEXT NOT NULL,
    stale INTEGER NOT NULL DEFAULT 0,
    stale_at TEXT NOT NULL DEFAULT '',
    UNIQUE(claim_id, knowledge_id, block_id, stance, source_revision)
);
CREATE INDEX IF NOT EXISTS idx_wiki_evidence_claim ON wiki_claim_evidence(claim_id);
CREATE INDEX IF NOT EXISTS idx_wiki_evidence_kid ON wiki_claim_evidence(knowledge_id);

CREATE TABLE IF NOT EXISTS wiki_page_claims (
    page_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    PRIMARY KEY (page_id, claim_id)
);
CREATE INDEX IF NOT EXISTS idx_wiki_page_claims_claim ON wiki_page_claims(claim_id);

CREATE TABLE IF NOT EXISTS wiki_dependencies (
    from_type TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_type TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    PRIMARY KEY (from_type, from_id, to_type, to_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_wiki_deps_from ON wiki_dependencies(from_type, from_id);
CREATE INDEX IF NOT EXISTS idx_wiki_deps_to ON wiki_dependencies(to_type, to_id);

CREATE TABLE IF NOT EXISTS wiki_projection_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS wiki_pages_v2_fts USING fts5(
    page_id UNINDEXED,
    title,
    content,
    tokenize='unicode61'
);

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
    weight REAL DEFAULT 1.0,
    UNIQUE(graph_id, source_knowledge_id, target_knowledge_id)
);

CREATE INDEX IF NOT EXISTS idx_graph_rel_src ON knowledge_graph_relations(graph_id, source_knowledge_id);
CREATE INDEX IF NOT EXISTS idx_graph_rel_tgt ON knowledge_graph_relations(graph_id, target_knowledge_id);

-- === Block graph model ===
CREATE TABLE IF NOT EXISTS blocks (
    id TEXT PRIMARY KEY,
    parent_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    page_id TEXT,
    content TEXT,
    block_type TEXT DEFAULT 'text',
    properties TEXT DEFAULT '{}',
    order_idx INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_blocks_page ON blocks(page_id);
CREATE INDEX IF NOT EXISTS idx_blocks_parent ON blocks(parent_id);

CREATE TABLE IF NOT EXISTS block_refs (
    source_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    target_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    ref_type TEXT DEFAULT 'link',
    PRIMARY KEY (source_id, target_id, ref_type)
);

CREATE TABLE IF NOT EXISTS entity_refs (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    ref_type TEXT DEFAULT 'mention',
    weight REAL DEFAULT 1.0,
    auto_discovered INTEGER DEFAULT 0,
    created_at TEXT,
    UNIQUE(source_type, source_id, target_type, target_id, ref_type)
);

CREATE INDEX IF NOT EXISTS idx_entity_refs_source ON entity_refs(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_entity_refs_target ON entity_refs(target_type, target_id);

CREATE TABLE IF NOT EXISTS block_property_index (
    block_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    prop_key TEXT,
    prop_value TEXT,
    value_type TEXT DEFAULT 'string',
    PRIMARY KEY (block_id, prop_key)
);

CREATE INDEX IF NOT EXISTS idx_prop_key_val ON block_property_index(prop_key, prop_value);

-- === Phase 2: Tag DAG, Property Schema, Effective Properties ===
CREATE TABLE IF NOT EXISTS tag_relations (
    parent_tag TEXT NOT NULL,
    child_tag TEXT NOT NULL,
    created_at TEXT,
    PRIMARY KEY (parent_tag, child_tag),
    CHECK(parent_tag <> child_tag)
);

CREATE INDEX IF NOT EXISTS idx_tag_relations_parent ON tag_relations(parent_tag);
CREATE INDEX IF NOT EXISTS idx_tag_relations_child ON tag_relations(child_tag);

CREATE TABLE IF NOT EXISTS property_schemas (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT DEFAULT '',
    property_name TEXT NOT NULL,
    property_type TEXT NOT NULL,
    required INTEGER DEFAULT 0,
    default_value TEXT,
    choices TEXT,
    constraints TEXT,
    created_at TEXT,
    UNIQUE(scope_type, scope_id, property_name)
);

CREATE INDEX IF NOT EXISTS idx_property_schemas_scope ON property_schemas(scope_type, scope_id);

CREATE TABLE IF NOT EXISTS effective_property_index (
    block_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    prop_key TEXT NOT NULL,
    prop_value TEXT,
    value_type TEXT DEFAULT 'string',
    source_type TEXT NOT NULL,
    source_id TEXT DEFAULT '',
    inherited INTEGER DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (block_id, prop_key)
);

CREATE INDEX IF NOT EXISTS idx_effective_prop_key_val ON effective_property_index(prop_key, prop_value);

CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (content_hash, model)
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    hashed TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    operator TEXT NOT NULL DEFAULT 'system',
    source TEXT NOT NULL DEFAULT 'mcp',
    snapshot_before TEXT,
    snapshot_after TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oplog_target ON operation_logs(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_oplog_time ON operation_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_oplog_operation ON operation_logs(operation);

-- === Phase 4: Agent Memory ===
CREATE TABLE IF NOT EXISTS agent_memory (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_key ON agent_memory(key);
CREATE INDEX IF NOT EXISTS idx_agent_memory_category ON agent_memory(category);
CREATE INDEX IF NOT EXISTS idx_agent_memory_updated ON agent_memory(updated_at);

CREATE VIRTUAL TABLE IF NOT EXISTS agent_memory_fts USING fts5(
    key, value,
    content=agent_memory,
    content_rowid=rowid,
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS agent_memory_ai AFTER INSERT ON agent_memory BEGIN
    INSERT INTO agent_memory_fts(rowid, key, value)
    VALUES (new.rowid, new.key, new.value);
END;

CREATE TRIGGER IF NOT EXISTS agent_memory_ad AFTER DELETE ON agent_memory BEGIN
    INSERT INTO agent_memory_fts(agent_memory_fts, rowid, key, value)
    VALUES ('delete', old.rowid, old.key, old.value);
END;

CREATE TRIGGER IF NOT EXISTS agent_memory_au AFTER UPDATE ON agent_memory BEGIN
    INSERT INTO agent_memory_fts(agent_memory_fts, rowid, key, value)
    VALUES ('delete', old.rowid, old.key, old.value);
    INSERT INTO agent_memory_fts(rowid, key, value)
    VALUES (new.rowid, new.key, new.value);
END;

-- === M3: Indexed Files (Path Index) ===
CREATE TABLE IF NOT EXISTS indexed_files (
    path TEXT PRIMARY KEY,
    knowledge_id TEXT,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    last_indexed_at TEXT,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_indexed_files_status ON indexed_files(status);
CREATE INDEX IF NOT EXISTS idx_indexed_files_knowledge ON indexed_files(knowledge_id);

-- === Version Conflict Cleanup (i001) ===
CREATE TABLE IF NOT EXISTS conflict_sessions (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'scanning',
    total_items_scanned INTEGER DEFAULT 0,
    candidates_found INTEGER DEFAULT 0,
    pairs_judged INTEGER DEFAULT 0,
    pairs_deleted INTEGER DEFAULT 0,
    pairs_ignored INTEGER DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS conflict_pairs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    item_a_id TEXT NOT NULL,
    item_b_id TEXT NOT NULL,
    candidate_source TEXT NOT NULL,
    similarity_score REAL,
    relation_type TEXT,
    newer_item_id TEXT,
    confidence REAL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    judged_at TEXT,
    resolved_at TEXT,
    FOREIGN KEY (session_id) REFERENCES conflict_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_conflict_pairs_session ON conflict_pairs(session_id);
CREATE INDEX IF NOT EXISTS idx_conflict_pairs_status ON conflict_pairs(status);
CREATE INDEX IF NOT EXISTS idx_conflict_pairs_items ON conflict_pairs(item_a_id, item_b_id);

CREATE TABLE IF NOT EXISTS conflict_ignores (
    id TEXT PRIMARY KEY,
    item_a_id TEXT NOT NULL,
    item_b_id TEXT NOT NULL,
    pair_key TEXT NOT NULL,
    ignored_at TEXT NOT NULL,
    source_pair_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_conflict_ignores_pair ON conflict_ignores(pair_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conflict_ignores_pair_unique ON conflict_ignores(pair_key);

-- === Verified Hybrid Maintenance Control Plane (j003) ===
CREATE TABLE IF NOT EXISTS maintenance_source_events (
    event_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    knowledge_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    source_path TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS maintenance_jobs (
    job_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    status TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    created_at TEXT NOT NULL,
    lease_until TEXT,
    due_at TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_maintenance_jobs_status_due ON maintenance_jobs(status, due_at, created_at);
CREATE TABLE IF NOT EXISTS maintenance_reviews (
    review_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    job_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_maintenance_reviews_status ON maintenance_reviews(status, created_at);
CREATE TABLE IF NOT EXISTS maintenance_dead_letters (
    job_id TEXT PRIMARY KEY,
    failed_at TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS maintenance_health_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS maintenance_schedules (
    schedule_name TEXT PRIMARY KEY,
    next_run_at TEXT,
    lease_until TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

"""


def apply_legacy_schema(conn: sqlite3.Connection) -> None:
    """Execute full CREATE IF NOT EXISTS baseline (test/compat only)."""
    conn.executescript(LEGACY_SCHEMA_SQL)


def apply_legacy_column_migrate(conn: sqlite3.Connection) -> None:
    """检查并补齐旧数据库缺失的列"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_items)").fetchall()}
    if "version" not in cols:
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN version INTEGER DEFAULT 1")
    if "file_size" not in cols:
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN file_size INTEGER DEFAULT 0")
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN content_hash TEXT DEFAULT ''")
    if "quality" not in cols:
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN quality TEXT DEFAULT ''")
    if "file_created_at" not in cols:
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN file_created_at TEXT DEFAULT ''")
    if "file_modified_at" not in cols:
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN file_modified_at TEXT DEFAULT ''")
    if "deleted_at" not in cols:
        # Sprint 3 / Phase 4: 软删除列
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN deleted_at TEXT DEFAULT NULL")
    if "quality_score" not in cols:
        # Phase 1 / data-heal: 内容质量评分列
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN quality_score INTEGER DEFAULT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_deleted ON knowledge_items(deleted_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_quality_score ON knowledge_items(quality_score)")

    msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
    if "source_graph" not in msg_cols:
        conn.execute(
            "ALTER TABLE chat_messages ADD COLUMN source_graph TEXT DEFAULT '{\"nodes\":[],\"edges\":[]}'"
        )

    # wiki_pages: complex_anomaly 字段（存储复杂异常类别，逗号分隔）
    wiki_cols = {row[1] for row in conn.execute("PRAGMA table_info(wiki_pages)").fetchall()}
    if "complex_anomaly" not in wiki_cols:
        conn.execute(
            "ALTER TABLE wiki_pages ADD COLUMN complex_anomaly TEXT DEFAULT ''"
        )

    ref_cols = {row[1] for row in conn.execute("PRAGMA table_info(entity_refs)").fetchall()}
    if "auto_discovered" not in ref_cols:
        conn.execute("ALTER TABLE entity_refs ADD COLUMN auto_discovered INTEGER DEFAULT 0")

    # 去重索引
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_hash ON knowledge_items(content_hash)"
    )

    # chunk_fts 重建：检测旧 schema（含 content=knowledge_chunks 或缺少 chunk_id）
    import logging as _logging
    chunk_fts_sql = conn.execute(
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
        conn.execute("DROP TABLE IF EXISTS chunk_fts")
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5("
            "fts_segmented, knowledge_id UNINDEXED, chunk_id UNINDEXED, tokenize='unicode61')"
        )
        conn.commit()
        _logging.getLogger(__name__).info("chunk_fts schema migrated, reindex needed")

    # 为 knowledge_graph_relations 添加 UNIQUE 约束（如旧表缺失）
    rel_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='knowledge_graph_relations'"
    ).fetchone()
    if rel_sql and 'UNIQUE' not in (rel_sql[0] or ''):
        # 重建表以添加唯一约束
        conn.execute("ALTER TABLE knowledge_graph_relations RENAME TO _old_graph_relations")
        conn.execute(
            """CREATE TABLE knowledge_graph_relations (
                id TEXT PRIMARY KEY,
                graph_id TEXT REFERENCES knowledge_graphs(id) ON DELETE CASCADE,
                source_knowledge_id TEXT NOT NULL,
                target_knowledge_id TEXT NOT NULL,
                relation_type TEXT DEFAULT 'related',
                description TEXT,
                weight REAL DEFAULT 1.0,
                UNIQUE(graph_id, source_knowledge_id, target_knowledge_id)
            )"""
        )
        conn.execute(
            """INSERT OR IGNORE INTO knowledge_graph_relations
               SELECT id, graph_id, source_knowledge_id, target_knowledge_id,
                      relation_type, description, weight
               FROM _old_graph_relations"""
        )
        conn.execute("DROP TABLE _old_graph_relations")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_graph_rel_src ON knowledge_graph_relations(graph_id, source_knowledge_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_graph_rel_tgt ON knowledge_graph_relations(graph_id, target_knowledge_id)"
        )
        conn.commit()
        _logging.getLogger(__name__).info("knowledge_graph_relations: added UNIQUE constraint")

    # Phase 2: 为旧数据库补建 tag_relations / property_schemas / effective_property_index
    conn.execute("""CREATE TABLE IF NOT EXISTS tag_relations (
        parent_tag TEXT NOT NULL,
        child_tag TEXT NOT NULL,
        created_at TEXT,
        PRIMARY KEY (parent_tag, child_tag),
        CHECK(parent_tag <> child_tag)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_relations_parent ON tag_relations(parent_tag)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_relations_child ON tag_relations(child_tag)")
    conn.execute("""CREATE TABLE IF NOT EXISTS property_schemas (
        id TEXT PRIMARY KEY,
        scope_type TEXT NOT NULL,
        scope_id TEXT DEFAULT '',
        property_name TEXT NOT NULL,
        property_type TEXT NOT NULL,
        required INTEGER DEFAULT 0,
        default_value TEXT,
        choices TEXT,
        constraints TEXT,
        created_at TEXT,
        UNIQUE(scope_type, scope_id, property_name)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_property_schemas_scope ON property_schemas(scope_type, scope_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS effective_property_index (
        block_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
        prop_key TEXT NOT NULL,
        prop_value TEXT,
        value_type TEXT DEFAULT 'string',
        source_type TEXT NOT NULL,
        source_id TEXT DEFAULT '',
        inherited INTEGER DEFAULT 0,
        updated_at TEXT,
        PRIMARY KEY (block_id, prop_key)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_effective_prop_key_val ON effective_property_index(prop_key, prop_value)")


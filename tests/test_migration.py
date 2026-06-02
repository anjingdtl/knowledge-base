import sqlite3
import subprocess
import sys
from pathlib import Path


def _seed_legacy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE knowledge_items (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT,
            source_type TEXT,
            source_path TEXT,
            file_type TEXT,
            tags TEXT,
            version INTEGER DEFAULT 1,
            file_size INTEGER DEFAULT 0,
            content_hash TEXT DEFAULT '',
            file_created_at TEXT DEFAULT '',
            file_modified_at TEXT DEFAULT '',
            quality TEXT DEFAULT '',
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        CREATE TABLE knowledge_chunks (
            id TEXT PRIMARY KEY,
            knowledge_id TEXT,
            chunk_index INTEGER,
            chunk_text TEXT,
            created_at TIMESTAMP
        );
        CREATE TABLE wiki_pages (
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
        CREATE TABLE wiki_links (
            source_page_id TEXT,
            target_page_id TEXT,
            link_type TEXT DEFAULT 'related',
            weight REAL DEFAULT 1.0,
            PRIMARY KEY (source_page_id, target_page_id)
        );
        CREATE TABLE knowledge_graphs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            source_type TEXT DEFAULT 'manual',
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        CREATE TABLE knowledge_graph_relations (
            id TEXT PRIMARY KEY,
            graph_id TEXT,
            source_knowledge_id TEXT NOT NULL,
            target_knowledge_id TEXT NOT NULL,
            relation_type TEXT DEFAULT 'related',
            description TEXT,
            weight REAL DEFAULT 1.0
        );
        """
    )
    conn.execute(
        "INSERT INTO knowledge_items (id, title, content, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("k1", "Knowledge", "content", "[]", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO knowledge_chunks (id, knowledge_id, chunk_index, chunk_text, created_at) VALUES (?, ?, ?, ?, ?)",
        ("c1", "k1", 0, "chunk content", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO wiki_pages (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("w1", "Wiki 1", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO wiki_pages (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("w2", "Wiki 2", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO wiki_links (source_page_id, target_page_id, link_type, weight) VALUES (?, ?, ?, ?)",
        ("w1", "w2", "related", 0.5),
    )
    conn.execute(
        "INSERT INTO knowledge_graphs (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("g1", "Graph", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        """INSERT INTO knowledge_graph_relations
           (id, graph_id, source_knowledge_id, target_knowledge_id, relation_type, description, weight)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("r1", "g1", "k1", "k2", "related", "desc", 0.9),
    )
    conn.commit()
    conn.close()


def test_block_graph_migration_dry_run_and_apply_are_idempotent(tmp_path):
    from scripts.migrate_to_block_graph import migrate_database

    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(db_path)

    dry = migrate_database(db_path, apply=False, backfill_missing_vectors=False, backup=False)
    assert dry["would_create_blocks"] == 1
    assert dry["would_create_entity_refs"] == 2

    applied = migrate_database(db_path, apply=True, backfill_missing_vectors=False, backup=False)
    assert applied["created_blocks"] == 1
    assert applied["created_entity_refs"] == 2

    second = migrate_database(db_path, apply=True, backfill_missing_vectors=False, backup=False)
    assert second["created_blocks"] == 0
    assert second["created_entity_refs"] == 0

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM entity_refs").fetchone()[0] == 2
    conn.close()


def test_migration_cli_accepts_explicit_dry_run(tmp_path):
    db_path = tmp_path / "legacy.db"
    _seed_legacy_db(db_path)

    script = Path(__file__).resolve().parents[1] / "scripts" / "migrate_to_block_graph.py"
    result = subprocess.run(
        [sys.executable, str(script), str(db_path), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "would_create_blocks: 1" in result.stdout
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'blocks'"
    ).fetchone() is None
    conn.close()

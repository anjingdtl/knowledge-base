"""Freeze Database._migrate() — no new schema mutations without Alembic."""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from src.services.db import Database

# Frozen inventory of historical runtime migrations (Phase-3).
# New schema → Alembic revision; do not grow these sets without policy review.
_FROZEN_ALTER_TABLES = frozenset({
    "knowledge_items",
    "chat_messages",
    "wiki_pages",
    "entity_refs",
    "knowledge_graph_relations",
})
_FROZEN_ALTER_COUNT = 12
_FROZEN_CREATE_INDEX_NAMES = frozenset({
    "idx_kb_deleted",
    "idx_kb_quality_score",
    "idx_knowledge_hash",
    "idx_graph_rel_src",
    "idx_graph_rel_tgt",
    "idx_tag_relations_parent",
    "idx_tag_relations_child",
    "idx_property_schemas_scope",
    "idx_effective_prop_key_val",
})
_FROZEN_CREATE_TABLES = frozenset({
    "chunk_fts",
    "knowledge_graph_relations",
    "tag_relations",
    "property_schemas",
    "effective_property_index",
})


def test_migrate_alter_tables_frozen():
    src = inspect.getsource(Database._migrate)
    alters = re.findall(r"ALTER TABLE (\w+)", src)
    assert len(alters) == _FROZEN_ALTER_COUNT, (
        f"_migrate() ALTER TABLE count changed: {len(alters)} != {_FROZEN_ALTER_COUNT}. "
        "Use Alembic for new schema."
    )
    assert set(alters) <= _FROZEN_ALTER_TABLES, (
        f"New ALTER TABLE targets: {set(alters) - _FROZEN_ALTER_TABLES}"
    )


def test_migrate_create_index_frozen():
    src = inspect.getsource(Database._migrate)
    indexes = set(re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", src))
    assert indexes == _FROZEN_CREATE_INDEX_NAMES, (
        f"CREATE INDEX set changed: {indexes} != {_FROZEN_CREATE_INDEX_NAMES}"
    )


def test_migrate_create_table_frozen():
    src = inspect.getsource(Database._migrate)
    tables = set(
        re.findall(r"CREATE (?:VIRTUAL )?TABLE(?: IF NOT EXISTS)? (\w+)", src)
    )
    assert tables == _FROZEN_CREATE_TABLES, (
        f"CREATE TABLE set changed: {tables} != {_FROZEN_CREATE_TABLES}"
    )


def test_migration_policy_doc_exists():
    path = Path("docs/architecture/database-migration-policy.md")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "Alembic" in text
    assert "_migrate" in text

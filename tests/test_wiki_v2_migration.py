"""Canonical Wiki v2 投影表迁移测试 (T2.1)。

验证:
- db.py _SCHEMA 建 6 表 + FTS,旧 wiki_* 表保留
- j001 alembic 迁移幂等(app schema 已建表后重入不报错)、空库可建、双向可回滚
- 复合主键 / UNIQUE 约束生效
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from src.services.db import _SCHEMA

_V2_TABLES = [
    "wiki_pages_v2", "wiki_claims", "wiki_claim_evidence",
    "wiki_page_claims", "wiki_dependencies", "wiki_projection_state",
]
_V2_FTS = "wiki_pages_v2_fts"
_LEGACY_WIKI_TABLES = ["wiki_pages", "wiki_links", "wiki_ops_log", "wiki_fts"]


def _load_j001():
    """动态加载 j001 迁移模块(避免 alembic 版本表依赖)。"""
    path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "j001_wiki_v2_projection.py"
    spec = importlib.util.spec_from_file_location("j001_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _table_names(db_path) -> set[str]:
    raw = sqlite3.connect(str(db_path))
    try:
        rows = raw.execute("SELECT name FROM sqlite_master WHERE type IN ('table')").fetchall()
    finally:
        raw.close()
    return {r[0] for r in rows}


def test_v2_tables_exist_after_schema(tmp_path):
    """app 启动 executescript(_SCHEMA) 后,6 表 + FTS 全部存在。"""
    db_path = tmp_path / "t.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_SCHEMA)
    raw.close()
    names = _table_names(db_path)
    for t in _V2_TABLES:
        assert t in names, f"missing table: {t}"
    assert _V2_FTS in names


def test_legacy_wiki_tables_preserved(tmp_path):
    """旧 wiki_pages/wiki_links/wiki_ops_log/wiki_fts 必须仍在(_SCHEMA 不删旧表)。"""
    db_path = tmp_path / "t.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_SCHEMA)
    raw.close()
    names = _table_names(db_path)
    for t in _LEGACY_WIKI_TABLES:
        assert t in names, f"legacy table dropped: {t}"


def test_schema_idempotent_repeated_executescript(tmp_path):
    """_SCHEMA 二次 executescript 不报错(IF NOT EXISTS 幂等)。"""
    db_path = tmp_path / "t.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_SCHEMA)
    raw.executescript(_SCHEMA)  # 二次必须不抛
    raw.close()


def test_j001_upgrade_idempotent_after_app_schema(tmp_path):
    """app schema 已建 v2 表后,j001 upgrade 必须幂等不报错(仿 i001 测试)。"""
    db_path = tmp_path / "t.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_SCHEMA)
    raw.close()

    engine = create_engine(f"sqlite:///{db_path}")
    j001 = _load_j001()
    with engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        with Operations.context(mc):
            j001.upgrade()
            j001.upgrade()  # 二次重入同样幂等


def test_j001_upgrade_on_empty_db_creates_v2_tables(tmp_path):
    """空库(未跑 _SCHEMA)上 j001 upgrade 也能建出 6 表 + FTS。"""
    db_path = tmp_path / "empty.db"
    engine = create_engine(f"sqlite:///{db_path}")
    j001 = _load_j001()
    with engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        with Operations.context(mc):
            j001.upgrade()
        conn.commit()
    names = _table_names(db_path)
    for t in _V2_TABLES:
        assert t in names
    assert _V2_FTS in names


def test_j001_downgrade_drops_v2_keeps_legacy(tmp_path):
    """upgrade 后 downgrade,v2 表消失,旧 wiki_* 表仍在。"""
    db_path = tmp_path / "t.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_SCHEMA)  # app 启动建全部表(含旧 wiki_*)
    raw.close()

    engine = create_engine(f"sqlite:///{db_path}")
    j001 = _load_j001()
    with engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        with Operations.context(mc):
            j001.upgrade()
            j001.downgrade()
        conn.commit()
    names = _table_names(db_path)
    for t in _V2_TABLES:
        assert t not in names, f"v2 table not dropped: {t}"
    assert _V2_FTS not in names
    for t in _LEGACY_WIKI_TABLES:
        assert t in names, f"legacy table wrongly dropped: {t}"


def test_evidence_unique_constraint(tmp_path):
    """wiki_claim_evidence 的 (claim,kid,block,stance,src_rev) UNIQUE 生效(block_id 非 NULL)。"""
    db_path = tmp_path / "t.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_SCHEMA)
    raw.close()
    engine = create_engine(f"sqlite:///{db_path}")
    j001 = _load_j001()
    with engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        with Operations.context(mc):
            j001.upgrade()
        conn.commit()
        conn.execute(text(
            "INSERT INTO wiki_claim_evidence(evidence_id, claim_id, stance, knowledge_id, "
            "block_id, location_json, source_revision, observed_at) "
            "VALUES ('e1','c1','supports','k1','b1','{}','r1','2026-01-01')"
        ))
        with pytest.raises(Exception):  # sqlite3.IntegrityError 经 sqlalchemy 包装
            conn.execute(text(
                "INSERT INTO wiki_claim_evidence(evidence_id, claim_id, stance, knowledge_id, "
                "block_id, location_json, source_revision, observed_at) "
                "VALUES ('e2','c1','supports','k1','b1','{}','r1','2026-01-01')"
            ))


def test_dependencies_composite_pk(tmp_path):
    """wiki_dependencies 5 列复合主键生效。"""
    db_path = tmp_path / "t.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_SCHEMA)
    raw.close()
    engine = create_engine(f"sqlite:///{db_path}")
    j001 = _load_j001()
    with engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        with Operations.context(mc):
            j001.upgrade()
        conn.commit()
        conn.execute(text(
            "INSERT INTO wiki_dependencies(from_type,from_id,to_type,to_id,relation) "
            "VALUES ('source','s1','evidence','e1','produces')"
        ))
        with pytest.raises(Exception):
            conn.execute(text(
                "INSERT INTO wiki_dependencies(from_type,from_id,to_type,to_id,relation) "
                "VALUES ('source','s1','evidence','e1','produces')"
            ))


def test_page_claims_composite_pk(tmp_path):
    """wiki_page_claims (page_id, claim_id) 复合主键生效。"""
    db_path = tmp_path / "t.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_SCHEMA)
    raw.close()
    engine = create_engine(f"sqlite:///{db_path}")
    j001 = _load_j001()
    with engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        with Operations.context(mc):
            j001.upgrade()
        conn.commit()
        conn.execute(text(
            "INSERT INTO wiki_page_claims(page_id, claim_id, display_order) "
            "VALUES ('p1', 'c1', 0)"
        ))
        with pytest.raises(Exception):
            conn.execute(text(
                "INSERT INTO wiki_page_claims(page_id, claim_id, display_order) "
                "VALUES ('p1', 'c1', 1)"
            ))


def test_projection_state_keyvalue(tmp_path):
    """wiki_projection_state 是 key-value 表,可按 key 读写。"""
    db_path = tmp_path / "t.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_SCHEMA)
    raw.close()
    engine = create_engine(f"sqlite:///{db_path}")
    j001 = _load_j001()
    with engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        with Operations.context(mc):
            j001.upgrade()
        conn.commit()
        conn.execute(text("INSERT INTO wiki_projection_state(key, value) VALUES ('schema_version', '1')"))
        conn.execute(text("INSERT INTO wiki_projection_state(key, value) VALUES ('projection_status', 'ok')"))
        conn.commit()
        rows = conn.execute(text("SELECT value FROM wiki_projection_state WHERE key='projection_status'")).fetchone()
        assert rows[0] == "ok"
        with pytest.raises(Exception):
            conn.execute(text("INSERT INTO wiki_projection_state(key, value) VALUES ('schema_version', '2')"))

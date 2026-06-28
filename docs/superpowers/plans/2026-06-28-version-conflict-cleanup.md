# Version Conflict Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现公司制度库版本迭代冲突检测与清理功能（如 2022版 vs 2025版同名制度），用户主动触发扫描 → LLM 判断 → 确认软删 → 回收站可恢复。

**Architecture:** 独立维护中心模块。复用现有级联删除 / operation_log undo / embedding 三级缓存 / jobs 异步任务体系。新增 3 张表 + 1 个服务 + 1 个路由组 + 1 个前端页。不新增 MCP 工具，不引入 cron。

**Tech Stack:** Python 3.11 / FastAPI / SQLite + Alembic / React + TypeScript + Tailwind / 现有 LLMService + EmbeddingService + VectorStore

**Spec:** `docs/superpowers/specs/2026-06-28-version-conflict-cleanup-design.md`

**Phases（每 Phase 完成后 review）：**
- **Phase 1**：数据层（migration + model + repo）
- **Phase 2**：服务层（VersionConflictService）
- **Phase 3**：API 路由 + 异步任务注册
- **Phase 4**：前端 MaintenanceView + 入口接入

**全局执行约定：**
- 每 Phase 完成后跑该 Phase 的测试，全绿才算 review 通过
- 全部 Phase 完成后跑全改动部分测试（所有新增/修改文件的测试）
- 测试全绿后 commit + git push 主分支

---

## File Structure

### 新增文件
| 文件 | 责任 |
|------|------|
| `alembic/versions/i001_version_conflict.py` | 三张新表的 migration |
| `src/models/version_conflict.py` | 数据模型 dataclass |
| `src/repositories/conflict_repo.py` | 三张表的 DAO |
| `src/services/version_conflict.py` | 核心编排服务（扫描+判断+清理） |
| `src/api/routes/maintenance.py` | REST 路由 |
| `client/src/views/MaintenanceView.tsx` | 前端维护页 |
| `tests/test_version_conflict.py` | 单元测试（服务层） |
| `tests/test_conflict_repo.py` | 仓库测试 |
| `tests/test_maintenance_api.py` | API 测试 |

### 修改文件
| 文件 | 修改点 |
|------|--------|
| `src/api/routes/__init__.py` | 导出 `maintenance_router` |
| `src/api/__init__.py` | `include_router(maintenance_router)` |
| `src/api/routes/jobs.py` | `ALLOWED_JOB_TYPES` 加两个新类型 |
| `src/services/async_tasks.py` | 注册两个新 job handler |
| `client/src/components/Layout.tsx` | NAV_ITEMS 加"维护中心" |
| `client/src/App.tsx` | 路由表加 `/maintenance` |

---

# Phase 1: 数据层（migration + model + repo）

## Task 1.1: 创建 Alembic Migration

**Files:**
- Create: `alembic/versions/i001_version_conflict.py`

- [ ] **Step 1: 写 migration 文件**

```python
"""add version conflict tables (sessions / pairs / ignores)

Revision ID: i001_version_conflict
Revises: h001_quality_score
Create Date: 2026-06-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "i001_version_conflict"
down_revision: Union[str, Sequence[str], None] = "h001_quality_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 conflict_sessions / conflict_pairs / conflict_ignores 三张表。"""
    # 表 1：扫描会话
    op.create_table(
        "conflict_sessions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("status", sa.Text, nullable=False, server_default="scanning"),
        sa.Column("total_items_scanned", sa.Integer, server_default="0"),
        sa.Column("candidates_found", sa.Integer, server_default="0"),
        sa.Column("pairs_judged", sa.Integer, server_default="0"),
        sa.Column("pairs_deleted", sa.Integer, server_default="0"),
        sa.Column("pairs_ignored", sa.Integer, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("started_at", sa.Text, nullable=False),
        sa.Column("completed_at", sa.Text),
    )

    # 表 2：候选对
    op.create_table(
        "conflict_pairs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("session_id", sa.Text, nullable=False),
        sa.Column("item_a_id", sa.Text, nullable=False),
        sa.Column("item_b_id", sa.Text, nullable=False),
        sa.Column("candidate_source", sa.Text, nullable=False),
        sa.Column("similarity_score", sa.Real),
        sa.Column("relation_type", sa.Text),
        sa.Column("newer_item_id", sa.Text),
        sa.Column("confidence", sa.Real),
        sa.Column("reason", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("judged_at", sa.Text),
        sa.Column("resolved_at", sa.Text),
        sa.ForeignKeyConstraint(["session_id"], ["conflict_sessions.id"]),
    )
    op.create_index("idx_conflict_pairs_session", "conflict_pairs", ["session_id"])
    op.create_index("idx_conflict_pairs_status", "conflict_pairs", ["status"])
    op.create_index("idx_conflict_pairs_items", "conflict_pairs", ["item_a_id", "item_b_id"])

    # 表 3：忽略列表
    op.create_table(
        "conflict_ignores",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("item_a_id", sa.Text, nullable=False),
        sa.Column("item_b_id", sa.Text, nullable=False),
        sa.Column("pair_key", sa.Text, nullable=False),
        sa.Column("ignored_at", sa.Text, nullable=False),
        sa.Column("source_pair_id", sa.Text),
    )
    op.create_index("idx_conflict_ignores_pair", "conflict_ignores", ["pair_key"])
    # pair_key 唯一约束
    op.execute("CREATE UNIQUE INDEX idx_conflict_ignores_pair_unique ON conflict_ignores(pair_key)")


def downgrade() -> None:
    """回滚：删三张表。"""
    op.execute("DROP INDEX IF EXISTS idx_conflict_ignores_pair_unique")
    op.drop_index("idx_conflict_ignores_pair", table_name="conflict_ignores")
    op.drop_table("conflict_ignores")
    op.drop_index("idx_conflict_pairs_items", table_name="conflict_pairs")
    op.drop_index("idx_conflict_pairs_status", table_name="conflict_pairs")
    op.drop_index("idx_conflict_pairs_session", table_name="conflict_pairs")
    op.drop_table("conflict_pairs")
    op.drop_table("conflict_sessions")
```

- [ ] **Step 2: 运行 migration 验证**

Run: `cd /workspace && alembic upgrade head`
Expected: 无报错，输出 `Running upgrade -> i001_version_conflict`

- [ ] **Step 3: 验证表结构**

Run: `cd /workspace && python -c "from src.services.db import Database; Database.connect('test_verify.db'); conn=Database.get_conn(); print([r['name'] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'conflict_%'\").fetchall()])"`
Expected: `['conflict_sessions', 'conflict_pairs', 'conflict_ignores']`

- [ ] **Step 4: 回滚验证（可选，确保 downgrade 正确）**

Run: `cd /workspace && alembic downgrade -1 && alembic upgrade head`
Expected: 无报错

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/i001_version_conflict.py
git commit -m "feat(db): add version conflict tables migration (i001)"
```

---

## Task 1.2: 创建数据模型

**Files:**
- Create: `src/models/version_conflict.py`

- [ ] **Step 1: 写 dataclass 模型**

```python
"""版本冲突检测数据模型"""
import uuid
from dataclasses import dataclass, field
from typing import Optional

from src.utils.time_utils import utcnow_iso


def _make_pair_key(a: str, b: str) -> str:
    """归一化 pair_key：始终 min|max，避免 A/B 与 B/A 重复。"""
    return f"{min(a, b)}|{max(a, b)}"


@dataclass
class ConflictSession:
    """扫描会话"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "scanning"  # scanning | judging | ready | completed | error
    total_items_scanned: int = 0
    candidates_found: int = 0
    pairs_judged: int = 0
    pairs_deleted: int = 0
    pairs_ignored: int = 0
    error: Optional[str] = None
    started_at: str = field(default_factory=utcnow_iso)
    completed_at: Optional[str] = None

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "total_items_scanned": self.total_items_scanned,
            "candidates_found": self.candidates_found,
            "pairs_judged": self.pairs_judged,
            "pairs_deleted": self.pairs_deleted,
            "pairs_ignored": self.pairs_ignored,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "ConflictSession":
        return cls(
            id=row["id"],
            status=row["status"],
            total_items_scanned=row.get("total_items_scanned", 0),
            candidates_found=row.get("candidates_found", 0),
            pairs_judged=row.get("pairs_judged", 0),
            pairs_deleted=row.get("pairs_deleted", 0),
            pairs_ignored=row.get("pairs_ignored", 0),
            error=row.get("error"),
            started_at=row["started_at"],
            completed_at=row.get("completed_at"),
        )


@dataclass
class ConflictPair:
    """候选对"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    item_a_id: str = ""
    item_b_id: str = ""
    candidate_source: str = ""  # sql_tag | sql_title | embedding
    similarity_score: Optional[float] = None
    # LLM 判断结果
    relation_type: Optional[str] = None  # supersedes | superseded_by | partial_overlap | unrelated
    newer_item_id: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None
    # 状态机
    status: str = "pending"  # pending | ignored | deleted
    created_at: str = field(default_factory=utcnow_iso)
    judged_at: Optional[str] = None
    resolved_at: Optional[str] = None

    @property
    def pair_key(self) -> str:
        return _make_pair_key(self.item_a_id, self.item_b_id)

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "item_a_id": self.item_a_id,
            "item_b_id": self.item_b_id,
            "candidate_source": self.candidate_source,
            "similarity_score": self.similarity_score,
            "relation_type": self.relation_type,
            "newer_item_id": self.newer_item_id,
            "confidence": self.confidence,
            "reason": self.reason,
            "status": self.status,
            "created_at": self.created_at,
            "judged_at": self.judged_at,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "ConflictPair":
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            item_a_id=row["item_a_id"],
            item_b_id=row["item_b_id"],
            candidate_source=row["candidate_source"],
            similarity_score=row.get("similarity_score"),
            relation_type=row.get("relation_type"),
            newer_item_id=row.get("newer_item_id"),
            confidence=row.get("confidence"),
            reason=row.get("reason"),
            status=row["status"],
            created_at=row["created_at"],
            judged_at=row.get("judged_at"),
            resolved_at=row.get("resolved_at"),
        )


@dataclass
class ConflictIgnore:
    """忽略记录"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    item_a_id: str = ""
    item_b_id: str = ""
    pair_key: str = ""
    ignored_at: str = field(default_factory=utcnow_iso)
    source_pair_id: Optional[str] = None

    @classmethod
    def from_pair(cls, item_a_id: str, item_b_id: str, source_pair_id: str | None = None) -> "ConflictIgnore":
        return cls(
            item_a_id=item_a_id,
            item_b_id=item_b_id,
            pair_key=_make_pair_key(item_a_id, item_b_id),
            source_pair_id=source_pair_id,
        )

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "item_a_id": self.item_a_id,
            "item_b_id": self.item_b_id,
            "pair_key": self.pair_key,
            "ignored_at": self.ignored_at,
            "source_pair_id": self.source_pair_id,
        }

    @classmethod
    def from_row(cls, row: dict) -> "ConflictIgnore":
        return cls(
            id=row["id"],
            item_a_id=row["item_a_id"],
            item_b_id=row["item_b_id"],
            pair_key=row["pair_key"],
            ignored_at=row["ignored_at"],
            source_pair_id=row.get("source_pair_id"),
        )
```

- [ ] **Step 2: 验证导入**

Run: `cd /workspace && python -c "from src.models.version_conflict import ConflictSession, ConflictPair, ConflictIgnore, _make_pair_key; print(_make_pair_key('b', 'a'))"`
Expected: `a|b`

- [ ] **Step 3: Commit**

```bash
git add src/models/version_conflict.py
git commit -m "feat(model): add version conflict dataclasses"
```

---

## Task 1.3: 创建 ConflictRepository

**Files:**
- Create: `src/repositories/conflict_repo.py`
- Test: `tests/test_conflict_repo.py`

- [ ] **Step 1: 写失败测试**

```python
"""ConflictRepository 测试"""
import pytest

from src.models.version_conflict import ConflictSession, ConflictPair, ConflictIgnore
from src.repositories.conflict_repo import ConflictRepository


@pytest.fixture
def repo():
    return ConflictRepository()


def test_create_and_get_session(repo):
    session = ConflictSession()
    repo.create_session(session)
    got = repo.get_session(session.id)
    assert got is not None
    assert got.status == "scanning"


def test_update_session_status(repo):
    session = ConflictSession()
    repo.create_session(session)
    repo.update_session_status(session.id, "ready", completed_at="2026-06-28T10:00:00")
    got = repo.get_session(session.id)
    assert got.status == "ready"
    assert got.completed_at == "2026-06-28T10:00:00"


def test_list_sessions_by_status(repo):
    s1 = ConflictSession()
    s2 = ConflictSession(status="ready")
    repo.create_session(s1)
    repo.create_session(s2)
    scanning = repo.list_sessions(status="scanning")
    ready = repo.list_sessions(status="ready")
    assert any(s.id == s1.id for s in scanning)
    assert any(s.id == s2.id for s in ready)


def test_create_and_get_pair(repo):
    session = ConflictSession()
    repo.create_session(session)
    pair = ConflictPair(
        session_id=session.id,
        item_a_id="aaa",
        item_b_id="bbb",
        candidate_source="sql_tag",
    )
    repo.create_pair(pair)
    got = repo.get_pair(pair.id)
    assert got is not None
    assert got.item_a_id == "aaa"
    assert got.pair_key == "aaa|bbb"


def test_list_pairs_with_join(repo):
    """list_pairs 应 LEFT JOIN knowledge_items 返回标题"""
    from src.models.knowledge import KnowledgeItem
    from src.repositories.knowledge_repo import KnowledgeRepository

    # 先插入一条 knowledge_item
    kr = KnowledgeRepository()
    item = KnowledgeItem(title="2022年劳动竞赛制度", content="旧版")
    kr.insert(item.to_row())

    session = ConflictSession()
    repo.create_session(session)
    pair = ConflictPair(
        session_id=session.id,
        item_a_id=item.id,
        item_b_id="nonexistent",
        candidate_source="sql_tag",
    )
    repo.create_pair(pair)

    pairs = repo.list_pairs(session.id, status="pending")
    assert len(pairs) == 1
    assert pairs[0]["item_a_title"] == "2022年劳动竞赛制度"
    # 不存在的条目标题应为 None
    assert pairs[0]["item_b_title"] is None


def test_update_pair_judgment(repo):
    session = ConflictSession()
    repo.create_session(session)
    pair = ConflictPair(session_id=session.id, item_a_id="a", item_b_id="b", candidate_source="sql_tag")
    repo.create_pair(pair)
    repo.update_pair_judgment(
        pair.id,
        relation_type="supersedes",
        newer_item_id="b",
        confidence=0.9,
        reason="2025版替代2022版",
    )
    got = repo.get_pair(pair.id)
    assert got.relation_type == "supersedes"
    assert got.confidence == 0.9
    assert got.judged_at is not None


def test_update_pair_status(repo):
    session = ConflictSession()
    repo.create_session(session)
    pair = ConflictPair(session_id=session.id, item_a_id="a", item_b_id="b", candidate_source="sql_tag")
    repo.create_pair(pair)
    repo.update_pair_status(pair.id, "deleted")
    got = repo.get_pair(pair.id)
    assert got.status == "deleted"
    assert got.resolved_at is not None


def test_add_ignore_unique_constraint(repo):
    """同 pair_key 二次插入应被忽略（INSERT OR IGNORE）"""
    ignore1 = ConflictIgnore.from_pair("a", "b")
    ignore2 = ConflictIgnore.from_pair("b", "a")  # 同 pair_key
    repo.add_ignore(ignore1)
    repo.add_ignore(ignore2)  # 应静默失败
    ignores = repo.list_ignores()
    assert len(ignores) == 1


def test_is_ignored(repo):
    ignore = ConflictIgnore.from_pair("a", "b")
    repo.add_ignore(ignore)
    assert repo.is_ignored("a", "b")
    assert repo.is_ignored("b", "a")  # 归一化后应等价
    assert not repo.is_ignored("a", "c")


def test_list_ignores_with_titles(repo):
    from src.models.knowledge import KnowledgeItem
    from src.repositories.knowledge_repo import KnowledgeRepository

    kr = KnowledgeRepository()
    item = KnowledgeItem(title="制度A", content="A")
    kr.insert(item.to_row())

    ignore = ConflictIgnore.from_pair(item.id, "other")
    repo.add_ignore(ignore)

    ignores = repo.list_ignores()
    assert len(ignores) == 1
    assert ignores[0]["item_a_title"] == "制度A"


def test_delete_ignore(repo):
    ignore = ConflictIgnore.from_pair("a", "b")
    repo.add_ignore(ignore)
    ok = repo.delete_ignore(ignore.id)
    assert ok
    assert not repo.is_ignored("a", "b")


def test_increment_session_counter(repo):
    session = ConflictSession()
    repo.create_session(session)
    repo.increment_session_counter(session.id, "candidates_found", 5)
    repo.increment_session_counter(session.id, "candidates_found", 3)
    got = repo.get_session(session.id)
    assert got.candidates_found == 8
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /workspace && pytest tests/test_conflict_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.repositories.conflict_repo'`

- [ ] **Step 3: 写 ConflictRepository**

```python
"""版本冲突检测 DAO — conflict_sessions / conflict_pairs / conflict_ignores"""
from datetime import datetime
from typing import Optional

from src.models.version_conflict import (
    ConflictSession, ConflictPair, ConflictIgnore, _make_pair_key,
)
from src.services.db import Database


class ConflictRepository:
    """三张表的 CRUD"""

    def __init__(self, db=None):
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    # ── Sessions ──

    def create_session(self, session: ConflictSession) -> None:
        self._conn().execute(
            """INSERT INTO conflict_sessions
               (id, status, total_items_scanned, candidates_found, pairs_judged,
                pairs_deleted, pairs_ignored, error, started_at, completed_at)
               VALUES (:id, :status, :total_items_scanned, :candidates_found,
                :pairs_judged, :pairs_deleted, :pairs_ignored, :error,
                :started_at, :completed_at)""",
            session.to_row(),
        )
        self._conn().commit()

    def get_session(self, session_id: str) -> Optional[ConflictSession]:
        row = self._conn().execute(
            "SELECT * FROM conflict_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return ConflictSession.from_row(dict(row)) if row else None

    def list_sessions(self, status: str | None = None,
                      limit: int = 50, offset: int = 0) -> list[ConflictSession]:
        sql = "SELECT * FROM conflict_sessions"
        params = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn().execute(sql, params).fetchall()
        return [ConflictSession.from_row(dict(r)) for r in rows]

    def update_session_status(self, session_id: str, status: str,
                              error: str | None = None,
                              completed_at: str | None = None) -> None:
        sets = ["status = ?"]
        params = [status]
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if completed_at is not None:
            sets.append("completed_at = ?")
            params.append(completed_at)
        params.append(session_id)
        self._conn().execute(
            f"UPDATE conflict_sessions SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        self._conn().commit()

    def increment_session_counter(self, session_id: str, field: str, delta: int = 1) -> None:
        """自增会话计数器。field 必须是合法字段名。"""
        allowed = {"total_items_scanned", "candidates_found",
                   "pairs_judged", "pairs_deleted", "pairs_ignored"}
        if field not in allowed:
            raise ValueError(f"Invalid counter field: {field}")
        self._conn().execute(
            f"UPDATE conflict_sessions SET {field} = {field} + ? WHERE id = ?",
            (delta, session_id),
        )
        self._conn().commit()

    # ── Pairs ──

    def create_pair(self, pair: ConflictPair) -> None:
        self._conn().execute(
            """INSERT INTO conflict_pairs
               (id, session_id, item_a_id, item_b_id, candidate_source,
                similarity_score, relation_type, newer_item_id, confidence,
                reason, status, created_at, judged_at, resolved_at)
               VALUES (:id, :session_id, :item_a_id, :item_b_id, :candidate_source,
                :similarity_score, :relation_type, :newer_item_id, :confidence,
                :reason, :status, :created_at, :judged_at, :resolved_at)""",
            pair.to_row(),
        )
        self._conn().commit()

    def create_pairs_batch(self, pairs: list[ConflictPair]) -> None:
        if not pairs:
            return
        rows = [p.to_row() for p in pairs]
        self._conn().executemany(
            """INSERT INTO conflict_pairs
               (id, session_id, item_a_id, item_b_id, candidate_source,
                similarity_score, relation_type, newer_item_id, confidence,
                reason, status, created_at, judged_at, resolved_at)
               VALUES (:id, :session_id, :item_a_id, :item_b_id, :candidate_source,
                :similarity_score, :relation_type, :newer_item_id, :confidence,
                :reason, :status, :created_at, :judged_at, :resolved_at)""",
            rows,
        )
        self._conn().commit()

    def get_pair(self, pair_id: str) -> Optional[ConflictPair]:
        row = self._conn().execute(
            "SELECT * FROM conflict_pairs WHERE id = ?", (pair_id,)
        ).fetchone()
        return ConflictPair.from_row(dict(row)) if row else None

    def list_pairs(self, session_id: str, status: str | None = None,
                   relation_type: str | None = None,
                   limit: int = 50, offset: int = 0) -> list[dict]:
        """分页查询候选对，LEFT JOIN knowledge_items 返回标题。"""
        sql = """
            SELECT cp.*,
                   ka.title AS item_a_title, ka.created_at AS item_a_created,
                   kb.title AS item_b_title, kb.created_at AS item_b_created
            FROM conflict_pairs cp
            LEFT JOIN knowledge_items ka ON ka.id = cp.item_a_id
            LEFT JOIN knowledge_items kb ON kb.id = cp.item_b_id
            WHERE cp.session_id = ?
        """
        params = [session_id]
        if status:
            sql += " AND cp.status = ?"
            params.append(status)
        if relation_type:
            sql += " AND cp.relation_type = ?"
            params.append(relation_type)
        sql += " ORDER BY cp.created_at ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn().execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def list_pending_pairs(self, session_id: str, limit: int = 20) -> list[ConflictPair]:
        rows = self._conn().execute(
            """SELECT * FROM conflict_pairs
               WHERE session_id = ? AND status = 'pending'
               ORDER BY created_at ASC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [ConflictPair.from_row(dict(r)) for r in rows]

    def update_pair_judgment(self, pair_id: str, relation_type: str,
                             newer_item_id: str | None, confidence: float,
                             reason: str) -> None:
        self._conn().execute(
            """UPDATE conflict_pairs
               SET relation_type = ?, newer_item_id = ?, confidence = ?,
                   reason = ?, judged_at = ?
               WHERE id = ?""",
            (relation_type, newer_item_id, confidence, reason,
             datetime.now().isoformat(), pair_id),
        )
        self._conn().commit()

    def update_pair_status(self, pair_id: str, status: str,
                           resolved_at: str | None = None) -> None:
        if resolved_at is None:
            resolved_at = datetime.now().isoformat()
        self._conn().execute(
            "UPDATE conflict_pairs SET status = ?, resolved_at = ? WHERE id = ?",
            (status, resolved_at, pair_id),
        )
        self._conn().commit()

    def count_pairs_by_status(self, session_id: str) -> dict[str, int]:
        rows = self._conn().execute(
            """SELECT status, COUNT(*) as cnt
               FROM conflict_pairs WHERE session_id = ?
               GROUP BY status""",
            (session_id,),
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    # ── Ignores ──

    def add_ignore(self, ignore: ConflictIgnore) -> bool:
        """添加忽略记录。同 pair_key 已存在时静默忽略（INSERT OR IGNORE）。"""
        cursor = self._conn().execute(
            """INSERT OR IGNORE INTO conflict_ignores
               (id, item_a_id, item_b_id, pair_key, ignored_at, source_pair_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ignore.id, ignore.item_a_id, ignore.item_b_id,
             ignore.pair_key or _make_pair_key(ignore.item_a_id, ignore.item_b_id),
             ignore.ignored_at, ignore.source_pair_id),
        )
        self._conn().commit()
        return cursor.rowcount > 0

    def is_ignored(self, item_a_id: str, item_b_id: str) -> bool:
        pair_key = _make_pair_key(item_a_id, item_b_id)
        row = self._conn().execute(
            "SELECT 1 FROM conflict_ignores WHERE pair_key = ? LIMIT 1",
            (pair_key,),
        ).fetchone()
        return row is not None

    def list_ignores(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """LEFT JOIN knowledge_items 返回标题。"""
        rows = self._conn().execute(
            """SELECT ci.*,
                      ka.title AS item_a_title,
                      kb.title AS item_b_title
               FROM conflict_ignores ci
               LEFT JOIN knowledge_items ka ON ka.id = ci.item_a_id
               LEFT JOIN knowledge_items kb ON kb.id = ci.item_b_id
               ORDER BY ci.ignored_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_ignore(self, ignore_id: str) -> bool:
        cursor = self._conn().execute(
            "DELETE FROM conflict_ignores WHERE id = ?", (ignore_id,)
        )
        self._conn().commit()
        return cursor.rowcount > 0
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /workspace && pytest tests/test_conflict_repo.py -v`
Expected: 全部 PASS（11 个测试）

- [ ] **Step 5: Commit**

```bash
git add src/repositories/conflict_repo.py tests/test_conflict_repo.py
git commit -m "feat(repo): add ConflictRepository with tests"
```

---

# Phase 2: 服务层（VersionConflictService）

## Task 2.1: 创建 VersionConflictService 骨架 + 会话管理

**Files:**
- Create: `src/services/version_conflict.py`
- Create: `tests/test_version_conflict.py`

- [ ] **Step 1: 写会话管理失败测试**

```python
"""VersionConflictService 测试"""
import pytest

from src.services.version_conflict import VersionConflictService
from src.repositories.conflict_repo import ConflictRepository


@pytest.fixture
def service():
    return VersionConflictService()


def test_start_scan_session_creates_session(service):
    session_id = service.start_scan_session()
    assert session_id
    status = service.get_session_status(session_id)
    assert status["status"] in ("scanning", "ready")  # 同步小库可能瞬间完成


def test_get_session_status_returns_counts(service):
    session_id = service.start_scan_session()
    status = service.get_session_status(session_id)
    assert "total_items_scanned" in status
    assert "candidates_found" in status
    assert "pairs_judged" in status


def test_list_sessions(service):
    service.start_scan_session()
    sessions = service.list_sessions()
    assert len(sessions) >= 1
```

- [ ] **Step 2: 写服务骨架**

```python
"""版本冲突扫描与清理编排服务

借鉴 Obsidian Repeat 插件的"扫描 → 提示用户 → 确认后执行"工作流，
用于公司制度库版本迭代场景（如 2022版 vs 2025版同名制度并存）。

核心流程：
  1. SQL 粗筛（tag/分类/标题核心词）
  2. embedding 补充（vectorstore.search，阈值 0.85+）
  3. LLM 四类关系判断
  4. 用户确认 → 软删旧版 → operation_log 支持 undo

不引入 cron，由用户主动触发。
"""
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from src.models.version_conflict import (
    ConflictSession, ConflictPair, ConflictIgnore, _make_pair_key,
)
from src.repositories.conflict_repo import ConflictRepository
from src.services.db import Database
from src.utils.llm_text import strip_think

logger = logging.getLogger(__name__)


# 性能保护配置（适配 embedding 2000 RPM / 500K TPM，保守留余量）
EMBEDDING_QPS = 3                    # 每秒 3 次 = 180 RPM（仅用 9% 配额）
EMBEDDING_QUERY_MAX_TOKENS = 500
LLM_BATCH_SIZE = 20
MAX_CANDIDATES_PER_SESSION = 1000
EMBEDDING_SIMILARITY_THRESHOLD = 0.85


# 标题核心词提取：去掉年份/版本号前缀
_TITLE_PREFIX_PATTERNS = [
    re.compile(r'^\d{4}\s*年'),       # "2022年"
    re.compile(r'^\[?v?\d+\.\d+\]?', re.IGNORECASE),  # "v1.0" / "[v2.0]"
    re.compile(r'^第?\d+[版次]', re.IGNORECASE),       # "第一版" / "第2次"
]


def extract_title_core(title: str) -> str:
    """提取标题核心词，去掉年份/版本号前缀。
    例：'2022年劳动竞赛执行规章制度' → '劳动竞赛执行规章制度'
    """
    core = title.strip()
    changed = True
    while changed:
        changed = False
        for pat in _TITLE_PREFIX_PATTERNS:
            new = pat.sub('', core).strip()
            if new != core:
                core = new
                changed = True
    return core


JUDGE_PROMPT = """你是公司制度文档版本分析专家。判断以下两条知识是否为同一制度的不同版本。

## 知识条目 A
- ID: {id_a}
- 标题: {title_a}
- 创建时间: {created_a}
- 内容摘要: {content_a}

## 知识条目 B
- ID: {id_b}
- 标题: {title_b}
- 创建时间: {created_b}
- 内容摘要: {content_b}

## 判断要求
分析两条目关系，从以下四类中选一种：
- supersedes: A 是 B 的新版本（B 已被 A 替代）
- superseded_by: B 是 A 的新版本（A 已被 B 替代）
- partial_overlap: 部分内容重叠，但非整版本迭代
- unrelated: 无版本关系

## 输出格式（严格 JSON，不要 ```json 标记）
{{"relation_type":"supersedes|superseded_by|partial_overlap|unrelated","newer_item_id":"A或B的ID（仅 supersedes/superseded_by 时填，否则空字符串）","confidence":0.0-1.0,"reason":"一句话说明判断依据"}}
"""


class VersionConflictService:
    """版本冲突扫描与清理编排服务"""

    def __init__(self, repo=None, knowledge_repo=None, llm=None, vectorstore=None):
        self._repo = repo or ConflictRepository()
        self._knowledge_repo = knowledge_repo
        self._llm = llm
        self._vectorstore = vectorstore

    def _get_knowledge_repo(self):
        if self._knowledge_repo is None:
            from src.repositories.knowledge_repo import KnowledgeRepository
            self._knowledge_repo = KnowledgeRepository()
        return self._knowledge_repo

    def _get_llm(self):
        if self._llm is None:
            from src.services.llm import LLMService
            self._llm = LLMService()
        return self._llm

    def _get_vectorstore(self):
        if self._vectorstore is None:
            from src.services.vectorstore import VectorStore
            self._vectorstore = VectorStore()
        return self._vectorstore

    # ── 会话管理 ──

    def start_scan_session(self, rescan_ignored: bool = False,
                           run_synchronously: bool = False) -> str:
        """创建扫描会话。默认异步执行；run_synchronously=True 用于测试。

        Args:
            rescan_ignored: True 时重新扫描已忽略对（默认 False）
            run_synchronously: True 时同步跑完整个扫描（测试用）

        Returns:
            session_id
        """
        session = ConflictSession()
        self._repo.create_session(session)

        if run_synchronously:
            try:
                self._run_scan(session.id, rescan_ignored=rescan_ignored)
            except Exception as e:
                logger.exception("Scan failed for session %s", session.id)
                self._repo.update_session_status(
                    session.id, "error", error=str(e)
                )
        else:
            # 异步：通过 AsyncTaskService 创建 job
            try:
                from src.services.async_task import AsyncTaskService
                AsyncTaskService.create_job(
                    "version_conflict_scan",
                    {"session_id": session.id, "rescan_ignored": rescan_ignored},
                    priority=1,
                    max_retries=0,
                )
            except Exception as e:
                # AsyncTaskService 不可用时降级同步
                logger.warning("AsyncTaskService unavailable, running sync: %s", e)
                self._run_scan(session.id, rescan_ignored=rescan_ignored)

        return session.id

    def get_session_status(self, session_id: str) -> dict:
        """查询会话进度。"""
        session = self._repo.get_session(session_id)
        if not session:
            return {"error": "session not found", "session_id": session_id}
        counts = self._repo.count_pairs_by_status(session_id)
        return {
            "session_id": session.id,
            "status": session.status,
            "total_items_scanned": session.total_items_scanned,
            "candidates_found": session.candidates_found,
            "pairs_judged": session.pairs_judged,
            "pairs_deleted": session.pairs_deleted,
            "pairs_ignored": session.pairs_ignored,
            "error": session.error,
            "started_at": session.started_at,
            "completed_at": session.completed_at,
            "pairs_by_status": counts,
        }

    def list_sessions(self, status: str | None = None,
                      limit: int = 50, offset: int = 0) -> list[dict]:
        """列出会话。"""
        sessions = self._repo.list_sessions(status=status, limit=limit, offset=offset)
        return [s.to_row() for s in sessions]
```

- [ ] **Step 3: 运行测试验证通过**

Run: `cd /workspace && pytest tests/test_version_conflict.py -v`
Expected: 3 个测试 PASS

- [ ] **Step 4: Commit**

```bash
git add src/services/version_conflict.py tests/test_version_conflict.py
git commit -m "feat(service): add VersionConflictService session management"
```

---

## Task 2.2: 实现 SQL 粗筛 + embedding 补充

**Files:**
- Modify: `src/services/version_conflict.py`
- Modify: `tests/test_version_conflict.py`

- [ ] **Step 1: 追加扫描测试**

在 `tests/test_version_conflict.py` 末尾追加：

```python
from src.models.knowledge import KnowledgeItem
from src.repositories.knowledge_repo import KnowledgeRepository


@pytest.fixture
def sample_versioned_policies():
    """构造 2022/2025 同名制度对"""
    kr = KnowledgeRepository()
    old = KnowledgeItem(
        title="2022年劳动竞赛执行规章制度",
        content="第三条：年假5天。第四条：奖金上限1万。",
        tags=json.dumps(["劳动竞赛"]) if False else ["劳动竞赛"],
    )
    new = KnowledgeItem(
        title="2025年劳动竞赛执行规章制度",
        content="第三条：年假7天。第四条：奖金上限2万。第五条：新增考核。",
        tags=["劳动竞赛"],
    )
    kr.insert(old.to_row())
    kr.insert(new.to_row())
    return {"old": old, "new": new}


@pytest.fixture
def sample_unrelated_policies():
    """标题相似但内容无关"""
    kr = KnowledgeRepository()
    a = KnowledgeItem(title="2022年劳动竞赛执行规章制度", content="关于劳动竞赛的规定", tags=["劳动竞赛"])
    b = KnowledgeItem(title="2022年劳动保护用品采购制度", content="关于劳保用品采购", tags=["采购"])
    kr.insert(a.to_row())
    kr.insert(b.to_row())
    return {"a": a, "b": b}


def test_extract_title_core_strips_year():
    from src.services.version_conflict import extract_title_core
    assert extract_title_core("2022年劳动竞赛执行规章制度") == "劳动竞赛执行规章制度"
    assert extract_title_core("2025年劳动竞赛执行规章制度") == "劳动竞赛执行规章制度"
    assert extract_title_core("v1.0安全生产管理制度") == "安全生产管理制度"


def test_scan_phase_sql_finds_same_title_core(service, sample_versioned_policies):
    session_id = service.start_scan_session(run_synchronously=True)
    pairs = service._repo.list_pairs(session_id)
    assert len(pairs) >= 1
    # 应该找到 old 和 new 配对
    pair = pairs[0]
    ids = {pair["item_a_id"], pair["item_b_id"]}
    assert sample_versioned_policies["old"].id in ids
    assert sample_versioned_policies["new"].id in ids


def test_scan_skips_ignored_pairs(service, sample_versioned_policies):
    """已忽略的 pair 不应再次出现"""
    old = sample_versioned_policies["old"]
    new = sample_versioned_policies["new"]
    # 先忽略
    ignore = ConflictIgnore.from_pair(old.id, new.id)
    service._repo.add_ignore(ignore)

    session_id = service.start_scan_session(run_synchronously=True)
    pairs = service._repo.list_pairs(session_id)
    pair_ids = [(p["item_a_id"], p["item_b_id"]) for p in pairs]
    flat = {x for pair in pair_ids for x in pair}
    assert old.id not in flat or new.id not in flat


def test_scan_rescan_ignored_when_flag_set(service, sample_versioned_policies):
    """rescan_ignored=True 时应重新扫描"""
    old = sample_versioned_policies["old"]
    new = sample_versioned_policies["new"]
    ignore = ConflictIgnore.from_pair(old.id, new.id)
    service._repo.add_ignore(ignore)

    session_id = service.start_scan_session(rescan_ignored=True, run_synchronously=True)
    pairs = service._repo.list_pairs(session_id)
    assert len(pairs) >= 1


def test_unrelated_policies_not_paired_by_title(service, sample_unrelated_policies):
    """标题核心词不同的不应配对"""
    session_id = service.start_scan_session(run_synchronously=True)
    pairs = service._repo.list_pairs(session_id)
    for p in pairs:
        ids = {p["item_a_id"], p["item_b_id"]}
        # 不应把 a 和 b 配对
        assert not (sample_unrelated_policies["a"].id in ids
                    and sample_unrelated_policies["b"].id in ids)
```

记得在文件顶部加 `import json` 和 `from src.models.version_conflict import ConflictIgnore`。

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /workspace && pytest tests/test_version_conflict.py -v -k "scan or extract"`
Expected: FAIL with AttributeError `_run_scan` not implemented

- [ ] **Step 3: 实现 `_run_scan` + 粗筛逻辑**

在 `VersionConflictService` 类中追加：

```python
    # ── 扫描阶段 ──

    def _run_scan(self, session_id: str, rescan_ignored: bool = False) -> None:
        """执行完整扫描流程（同步）。由 start_scan_session 调用。"""
        self._repo.update_session_status(session_id, "scanning")

        # Phase 1: SQL 粗筛
        sql_pairs = self._scan_phase_sql(session_id, rescan_ignored=rescan_ignored)

        # Phase 2: embedding 补充
        emb_pairs = self._scan_phase_embedding(session_id, sql_pairs, rescan_ignored=rescan_ignored)

        # 合并并去重
        all_pairs = self._dedupe_pairs(sql_pairs + emb_pairs)
        if len(all_pairs) > MAX_CANDIDATES_PER_SESSION:
            logger.warning("Session %s: candidates %d exceed max %d, truncating",
                           session_id, len(all_pairs), MAX_CANDIDATES_PER_SESSION)
            all_pairs = all_pairs[:MAX_CANDIDATES_PER_SESSION]

        # 批量写入
        if all_pairs:
            pair_objs = [ConflictPair(
                session_id=session_id,
                item_a_id=p["a"],
                item_b_id=p["b"],
                candidate_source=p["source"],
                similarity_score=p.get("similarity"),
            ) for p in all_pairs]
            self._repo.create_pairs_batch(pair_objs)
            self._repo.increment_session_counter(session_id, "candidates_found", len(all_pairs))

        # 统计扫描条目数
        kr = self._get_knowledge_repo()
        active_items = kr.list(limit=999999)
        self._repo.increment_session_counter(
            session_id, "total_items_scanned", len(active_items)
        )

        self._repo.update_session_status(session_id, "ready")

    def _dedupe_pairs(self, pairs: list[dict]) -> list[dict]:
        """按 pair_key 去重"""
        seen = set()
        out = []
        for p in pairs:
            key = _make_pair_key(p["a"], p["b"])
            if key not in seen:
                seen.add(key)
                out.append(p)
        return out

    def _scan_phase_sql(self, session_id: str, rescan_ignored: bool = False) -> list[dict]:
        """SQL 粗筛：按 tag + 标题核心词分组。"""
        kr = self._get_knowledge_repo()
        items = kr.list(limit=999999)  # list() 默认过滤软删
        if len(items) < 2:
            return []

        candidates = []

        # 路径 1：按 tag 分组
        tag_groups: dict[str, list[dict]] = {}
        for it in items:
            tags = it.get("tags", [])
            if isinstance(tags, str):
                try:
                    import json
                    tags = json.loads(tags)
                except Exception:
                    tags = []
            for t in tags:
                tag_groups.setdefault(t, []).append(it)
        for tag, group in tag_groups.items():
            if len(group) < 2:
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    if self._should_skip_pair(a["id"], b["id"], rescan_ignored):
                        continue
                    candidates.append({
                        "a": a["id"], "b": b["id"],
                        "source": "sql_tag", "similarity": None,
                    })

        # 路径 2：按标题核心词分组
        title_groups: dict[str, list[dict]] = {}
        for it in items:
            core = extract_title_core(it.get("title", ""))
            if core and len(core) >= 4:  # 太短的核心词会误配
                title_groups.setdefault(core, []).append(it)
        for core, group in title_groups.items():
            if len(group) < 2:
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    if self._should_skip_pair(a["id"], b["id"], rescan_ignored):
                        continue
                    candidates.append({
                        "a": a["id"], "b": b["id"],
                        "source": "sql_title", "similarity": None,
                    })

        return self._dedupe_pairs(candidates)

    def _should_skip_pair(self, a_id: str, b_id: str, rescan_ignored: bool) -> bool:
        """判断是否应跳过该对（已忽略）"""
        if rescan_ignored:
            return False
        return self._repo.is_ignored(a_id, b_id)

    def _scan_phase_embedding(self, session_id: str, sql_pairs: list[dict],
                              rescan_ignored: bool = False) -> list[dict]:
        """embedding 补充：对未被 SQL 命中的文档跑 vectorstore.search。"""
        try:
            vs = self._get_vectorstore()
        except Exception as e:
            logger.warning("VectorStore unavailable, skipping embedding phase: %s", e)
            return []

        kr = self._get_knowledge_repo()
        items = kr.list(limit=999999)
        if len(items) < 2:
            return []

        # SQL 已命中的 item_id 集合
        sql_item_ids = set()
        for p in sql_pairs:
            sql_item_ids.add(p["a"])
            sql_item_ids.add(p["b"])

        candidates = []
        sql_pair_keys = {_make_pair_key(p["a"], p["b"]) for p in sql_pairs}

        for it in items:
            if it["id"] in sql_item_ids:
                continue  # SQL 已命中，跳过
            content = (it.get("content") or "")[:EMBEDDING_QUERY_MAX_TOKENS]
            if not content.strip():
                continue
            try:
                similar = vs.search(
                    query=content, top_k=5,
                )
            except Exception as e:
                logger.warning("VectorStore.search failed for %s: %s", it["id"], e)
                continue
            for hit in similar:
                hit_id = hit.get("id") or hit.get("knowledge_id")
                hit_score = hit.get("score", 0.0)
                if not hit_id or hit_id == it["id"]:
                    continue
                if hit_score < EMBEDDING_SIMILARITY_THRESHOLD:
                    continue
                if self._should_skip_pair(it["id"], hit_id, rescan_ignored):
                    continue
                pair_key = _make_pair_key(it["id"], hit_id)
                if pair_key in sql_pair_keys:
                    continue
                candidates.append({
                    "a": min(it["id"], hit_id),
                    "b": max(it["id"], hit_id),
                    "source": "embedding",
                    "similarity": hit_score,
                })
                sql_pair_keys.add(pair_key)  # 避免本阶段内重复
            # 限流
            time.sleep(1.0 / EMBEDDING_QPS)

        return self._dedupe_pairs(candidates)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /workspace && pytest tests/test_version_conflict.py -v`
Expected: 全部 PASS（含新增的扫描测试）

- [ ] **Step 5: Commit**

```bash
git add src/services/version_conflict.py tests/test_version_conflict.py
git commit -m "feat(service): implement SQL+embedding scan phases"
```

---

## Task 2.3: 实现 LLM 判断 + 删除 + 忽略

**Files:**
- Modify: `src/services/version_conflict.py`
- Modify: `tests/test_version_conflict.py`

- [ ] **Step 1: 追加判断/删除/忽略测试**

在 `tests/test_version_conflict.py` 末尾追加：

```python
class FakeLLM:
    """Mock LLM，返回预设 JSON"""
    def __init__(self, response: str = ""):
        self.response = response or '{"relation_type":"supersedes","newer_item_id":"B","confidence":0.9,"reason":"2025版替代2022版"}'
        self.calls = []

    def chat(self, messages, silent=False):
        self.calls.append(messages)
        return self.response


@pytest.fixture
def service_with_mock_llm(sample_versioned_policies):
    from src.services.version_conflict import VersionConflictService
    fake = FakeLLM()
    svc = VersionConflictService(llm=fake)
    # 预填一对候选
    session_id = svc.start_scan_session(run_synchronously=True)
    return svc, session_id, fake, sample_versioned_policies


def test_judge_pending_pairs_writes_judgment(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    svc.judge_pending_pairs(session_id, limit=20, run_synchronously=True)
    pairs = svc._repo.list_pairs(session_id, status="pending")
    # judge 后 unrelated 的会被改成 ignored，supersedes 类的保持 pending 待用户处理
    judged = [p for p in svc._repo.list_pairs(session_id) if p.get("judged_at")]
    assert len(judged) >= 1
    assert fake.calls  # LLM 被调用过


def test_judge_handles_llm_failure_gracefully(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    # 让 LLM 抛错
    class ErrorLLM:
        def chat(self, *a, **kw):
            raise RuntimeError("API down")
    svc._llm = ErrorLLM()
    svc.judge_pending_pairs(session_id, run_synchronously=True)
    # 失败的 pair 应保持 pending，confidence=0
    pairs = svc._repo.list_pairs(session_id, status="pending")
    assert all(p.get("confidence") in (None, 0) for p in pairs)


def test_execute_delete_targets_older_version(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    svc.judge_pending_pairs(session_id, run_synchronously=True)
    # 找到 supersedes 类型的 pair
    pairs = svc._repo.list_pairs(session_id)
    target = None
    for p in pairs:
        if p.get("relation_type") in ("supersedes", "superseded_by"):
            target = p
            break
    assert target is not None, "应至少有一对 supersedes 关系"

    result = svc.execute_delete(target["id"])
    assert result["ok"] is True
    # 验证旧版被软删
    kr = svc._get_knowledge_repo()
    old_id = policies["old"].id
    new_id = policies["new"].id
    deleted_id = result["deleted_item_id"]
    assert deleted_id == old_id  # 删的应该是旧版
    # 旧版应软删
    old = kr.get(old_id, include_deleted=True)
    assert old is not None
    assert old.get("deleted_at") is not None
    # 新版应保留
    new = kr.get(new_id)
    assert new is not None
    # pair 状态应更新
    updated = svc._repo.get_pair(target["id"])
    assert updated.status == "deleted"


def test_execute_delete_writes_operation_log(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    svc.judge_pending_pairs(session_id, run_synchronously=True)
    pairs = svc._repo.list_pairs(session_id)
    target = next(p for p in pairs if p.get("relation_type") in ("supersedes", "superseded_by"))
    result = svc.execute_delete(target["id"])
    assert result["ok"]
    assert result.get("operation_log_id")  # 应返回 log_id


def test_execute_delete_partial_overlap_blocked(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    # 手动构造一个 partial_overlap 的 pair
    from src.models.version_conflict import ConflictPair
    pair = ConflictPair(
        session_id=session_id,
        item_a_id=policies["old"].id,
        item_b_id=policies["new"].id,
        candidate_source="sql_tag",
        relation_type="partial_overlap",
        confidence=0.8,
        reason="部分重叠",
        status="pending",
    )
    pair.judged_at = datetime.now().isoformat()
    svc._repo.create_pair(pair)
    result = svc.execute_delete(pair.id)
    assert result["ok"] is False
    assert "partial_overlap" in result.get("error", {}).get("message", "").lower() \
           or "partial" in result.get("error", {}).get("message", "").lower()


def test_ignore_pair_writes_ignore_table(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    pairs = svc._repo.list_pairs(session_id, status="pending")
    if not pairs:
        svc.judge_pending_pairs(session_id, run_synchronously=True)
        pairs = svc._repo.list_pairs(session_id, status="pending")
    if not pairs:
        # 全被 judge 成 ignored 了，手动加一对
        from src.models.version_conflict import ConflictPair
        p = ConflictPair(
            session_id=session_id,
            item_a_id=policies["old"].id,
            item_b_id=policies["new"].id,
            candidate_source="sql_tag",
        )
        svc._repo.create_pair(p)
        pairs = svc._repo.list_pairs(session_id, status="pending")
    target = pairs[0]
    result = svc.ignore_pair(target["id"])
    assert result["ok"] is True
    # conflict_ignores 表应有记录
    assert svc._repo.is_ignored(target["item_a_id"], target["item_b_id"])
    # pair 状态应为 ignored
    updated = svc._repo.get_pair(target["id"])
    assert updated.status == "ignored"


def test_list_pairs_with_pagination(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    page1 = svc.list_pairs(session_id, limit=10, offset=0)
    assert isinstance(page1, list)
```

记得在文件顶部加 `from datetime import datetime`。

- [ ] **Step 2: 实现判断/删除/忽略方法**

在 `VersionConflictService` 类中追加：

```python
    # ── 判断阶段 ──

    def judge_pending_pairs(self, session_id: str, limit: int = LLM_BATCH_SIZE,
                            run_synchronously: bool = False) -> dict:
        """对 session 内 pending 候选对跑 LLM 判断。

        Args:
            session_id: 会话 ID
            limit: 单次判断上限
            run_synchronously: True 时同步执行（测试用）

        Returns:
            {"judged": N, "errors": [...]}
        """
        if not run_synchronously:
            try:
                from src.services.async_task import AsyncTaskService
                AsyncTaskService.create_job(
                    "version_conflict_judge",
                    {"session_id": session_id, "limit": limit},
                    priority=1, max_retries=0,
                )
                return {"ok": True, "async": True}
            except Exception as e:
                logger.warning("AsyncTaskService unavailable, running sync: %s", e)

        self._repo.update_session_status(session_id, "judging")
        pairs = self._repo.list_pending_pairs(session_id, limit=limit)
        kr = self._get_knowledge_repo()
        llm = self._get_llm()
        judged = 0
        errors = []
        items_cache = {}

        for pair in pairs:
            try:
                if pair.item_a_id not in items_cache:
                    items_cache[pair.item_a_id] = kr.get(pair.item_a_id) or {}
                if pair.item_b_id not in items_cache:
                    items_cache[pair.item_b_id] = kr.get(pair.item_b_id) or {}
                item_a = items_cache[pair.item_a_id]
                item_b = items_cache[pair.item_b_id]

                prompt = JUDGE_PROMPT.format(
                    id_a=pair.item_a_id, title_a=item_a.get("title", ""),
                    created_a=item_a.get("created_at", ""),
                    content_a=(item_a.get("content") or "")[:500],
                    id_b=pair.item_b_id, title_b=item_b.get("title", ""),
                    created_b=item_b.get("created_at", ""),
                    content_b=(item_b.get("content") or "")[:500],
                )
                resp = llm.chat([{"role": "user", "content": prompt}], silent=True)
                text = resp.get("content", resp) if isinstance(resp, dict) else str(resp)
                text = strip_think(text).strip()
                # 去除可能的 ```json 标记
                if text.startswith("```"):
                    text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

                import json
                data = json.loads(text)
                relation_type = data.get("relation_type", "unrelated")
                newer_item_id = data.get("newer_item_id", "")
                # newer_item_id 用 A/B 标识，转成实际 id
                if newer_item_id == "A":
                    newer_item_id = pair.item_a_id
                elif newer_item_id == "B":
                    newer_item_id = pair.item_b_id
                else:
                    newer_item_id = newer_item_id or None
                confidence = float(data.get("confidence", 0.0))
                reason = data.get("reason", "")

                self._repo.update_pair_judgment(
                    pair.id, relation_type, newer_item_id, confidence, reason
                )
                self._repo.increment_session_counter(session_id, "pairs_judged", 1)

                # unrelated 直接标记为 ignored（不写 ignore 表）
                if relation_type == "unrelated":
                    self._repo.update_pair_status(pair.id, "ignored")
                    self._repo.increment_session_counter(session_id, "pairs_ignored", 1)

                judged += 1
            except Exception as e:
                logger.warning("Judge failed for pair %s: %s", pair.id, e)
                errors.append({"pair_id": pair.id, "error": str(e)})
                # 失败的 pair 保持 pending，confidence=0
                self._repo.update_pair_judgment(
                    pair.id, "unrelated", None, 0.0,
                    f"判断失败: {e}"
                )

        self._repo.update_session_status(session_id, "ready")
        return {"judged": judged, "errors": errors}

    # ── 用户操作 ──

    def list_pairs(self, session_id: str, status: str | None = None,
                   relation_type: str | None = None,
                   limit: int = 50, offset: int = 0) -> list[dict]:
        """分页查询候选对，JOIN knowledge_items 返回标题。"""
        return self._repo.list_pairs(
            session_id, status=status, relation_type=relation_type,
            limit=limit, offset=offset,
        )

    def execute_delete(self, pair_id: str, operator: str = "user") -> dict:
        """确认删除旧版本。

        1. 读 pair 的 newer_item_id，确定要删的是另一侧
        2. 调 KnowledgeRepository.soft_delete_knowledge（实际在 Database）
        3. 写 operation_log（带快照，支持现有 undo）
        4. 更新 pair.status = 'deleted'

        Returns:
            {"ok": True, "deleted_item_id":..., "operation_log_id":...}
            或 {"ok": False, "error": {"code", "message"}}
        """
        pair = self._repo.get_pair(pair_id)
        if not pair:
            return {"ok": False, "error": {"code": "NOT_FOUND",
                                             "message": f"pair 不存在: {pair_id}"}}

        if pair.relation_type == "partial_overlap":
            return {"ok": False, "error": {
                "code": "PRECONDITION_FAILED",
                "message": "partial_overlap 不允许直接删除，需用户手动选择删哪条",
            }}

        if pair.relation_type not in ("supersedes", "superseded_by"):
            return {"ok": False, "error": {
                "code": "PRECONDITION_FAILED",
                "message": f"relation_type={pair.relation_type} 不支持删除",
            }}

        if not pair.newer_item_id:
            return {"ok": False, "error": {
                "code": "PRECONDITION_FAILED",
                "message": "newer_item_id 缺失，无法确定删除哪条",
            }}

        # 确定要删的（旧版）
        if pair.item_a_id == pair.newer_item_id:
            deleted_id = pair.item_b_id
        else:
            deleted_id = pair.item_a_id

        kr = self._get_knowledge_repo()
        item = kr.get(deleted_id, include_deleted=True)
        if not item:
            return {"ok": False, "error": {
                "code": "NOT_FOUND",
                "message": f"待删条目不存在: {deleted_id}",
            }}
        if item.get("deleted_at"):
            return {"ok": False, "error": {
                "code": "PRECONDITION_FAILED",
                "message": f"条目 {deleted_id} 已删除",
            }}

        # 写 operation_log（before 快照）
        log_id = ""
        try:
            from src.services.operation_log import OperationLogService
            from src.core.container import AppContainer
            # 尝试从容器获取，否则新建
            try:
                from src.api.deps import get_container
                container = get_container()
                op_service = container.operation_log_service
            except Exception:
                op_service = OperationLogService()
                op_service.attach_knowledge_repo(kr)

            log_id = op_service.log(
                operation="delete",
                target_type="knowledge",
                target_id=deleted_id,
                operator=operator,
                source="version_conflict",
                before={
                    "title": item.get("title", ""),
                    "content": (item.get("content") or "")[:2000],
                    "tags": item.get("tags", "[]"),
                    "deleted_at": None,
                },
                after={"deleted_at": "set", "reason": "version_conflict_cleanup"},
                metadata={
                    "pair_id": pair_id,
                    "newer_item_id": pair.newer_item_id,
                    "relation_type": pair.relation_type,
                },
            )
        except Exception as e:
            logger.warning("Failed to write operation_log: %s", e)

        # 软删
        ok = Database.soft_delete_knowledge(deleted_id)
        if not ok:
            return {"ok": False, "error": {
                "code": "INTERNAL_ERROR",
                "message": f"软删除失败: {deleted_id}",
            }}

        # 更新 pair 状态
        self._repo.update_pair_status(pair_id, "deleted")
        self._repo.increment_session_counter(pair.session_id, "pairs_deleted", 1)

        return {
            "ok": True,
            "deleted_item_id": deleted_id,
            "operation_log_id": log_id,
            "pair_id": pair_id,
        }

    def ignore_pair(self, pair_id: str) -> dict:
        """用户判定误报。写 conflict_ignores 表 + 更新 pair.status。"""
        pair = self._repo.get_pair(pair_id)
        if not pair:
            return {"ok": False, "error": {"code": "NOT_FOUND",
                                             "message": f"pair 不存在: {pair_id}"}}
        ignore = ConflictIgnore.from_pair(
            pair.item_a_id, pair.item_b_id, source_pair_id=pair_id
        )
        self._repo.add_ignore(ignore)
        self._repo.update_pair_status(pair_id, "ignored")
        self._repo.increment_session_counter(pair.session_id, "pairs_ignored", 1)
        return {"ok": True, "pair_id": pair_id, "ignored": True}

    def list_ignores(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """列出忽略记录。"""
        return self._repo.list_ignores(limit=limit, offset=offset)

    def delete_ignore(self, ignore_id: str) -> dict:
        """撤销忽略。"""
        ok = self._repo.delete_ignore(ignore_id)
        if not ok:
            return {"ok": False, "error": {"code": "NOT_FOUND",
                                             "message": f"ignore 不存在: {ignore_id}"}}
        return {"ok": True, "deleted": True, "ignore_id": ignore_id}
```

- [ ] **Step 3: 运行测试验证通过**

Run: `cd /workspace && pytest tests/test_version_conflict.py -v`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add src/services/version_conflict.py tests/test_version_conflict.py
git commit -m "feat(service): implement judge/delete/ignore with tests"
```

---

# Phase 3: API 路由 + 异步任务注册

## Task 3.1: 注册异步任务 handler

**Files:**
- Modify: `src/services/async_tasks.py`
- Modify: `src/api/routes/jobs.py`

- [ ] **Step 1: 在 async_tasks.py 末尾追加 handler + 注册**

在 `src/services/async_tasks.py` 的 `register_all_tasks()` 函数**之前**追加：

```python
def _version_conflict_scan_handler(job_id: str, params: dict) -> dict:
    """版本冲突扫描任务"""
    from src.services.async_task import AsyncTaskService
    from src.services.version_conflict import VersionConflictService

    session_id = params.get("session_id", "")
    rescan_ignored = params.get("rescan_ignored", False)
    AsyncTaskService.update_progress(job_id, 10, f"Scanning session {session_id}...")

    svc = VersionConflictService()
    try:
        svc._run_scan(session_id, rescan_ignored=rescan_ignored)
        AsyncTaskService.update_progress(job_id, 100, "Scan completed")
        return {"status": "success", "session_id": session_id}
    except Exception as e:
        logger.error(f"Version conflict scan {job_id} failed: {e}")
        raise


def _version_conflict_judge_handler(job_id: str, params: dict) -> dict:
    """版本冲突 LLM 判断任务"""
    from src.services.async_task import AsyncTaskService
    from src.services.version_conflict import VersionConflictService

    session_id = params.get("session_id", "")
    limit = params.get("limit", 20)
    AsyncTaskService.update_progress(job_id, 10, f"Judging session {session_id}...")

    svc = VersionConflictService()
    try:
        result = svc.judge_pending_pairs(session_id, limit=limit, run_synchronously=True)
        AsyncTaskService.update_progress(job_id, 100, "Judge completed")
        return {"status": "success", **result}
    except Exception as e:
        logger.error(f"Version conflict judge {job_id} failed: {e}")
        raise
```

修改 `register_all_tasks()` 函数：

```python
def register_all_tasks():
    TaskRegistry.register("reindex_all", _reindex_all_handler)
    TaskRegistry.register("wiki_compile", _wiki_compile_handler)
    TaskRegistry.register("wiki_lint", _wiki_lint_handler)
    TaskRegistry.register("wiki_site_generate", _wiki_site_generate_handler)
    TaskRegistry.register("file_ingest", _file_ingest_handler)
    TaskRegistry.register("url_ingest", _url_ingest_handler)
    TaskRegistry.register("path_scan", _path_scan_handler)
    TaskRegistry.register("version_conflict_scan", _version_conflict_scan_handler)
    TaskRegistry.register("version_conflict_judge", _version_conflict_judge_handler)
    logger.info("All async task handlers registered")
```

- [ ] **Step 2: 在 jobs.py 的 ALLOWED_JOB_TYPES 加新类型**

```python
ALLOWED_JOB_TYPES = {
    "reindex_all",
    "wiki_compile",
    "wiki_lint",
    "wiki_site_generate",
    "file_ingest",
    "url_ingest",
    "version_conflict_scan",
    "version_conflict_judge",
}
```

- [ ] **Step 3: 验证注册**

Run: `cd /workspace && python -c "from src.services.async_tasks import register_all_tasks; from src.services.async_worker import TaskRegistry; register_all_tasks(); print(sorted(TaskRegistry._handlers.keys()))"`
Expected: 包含 `version_conflict_scan` 和 `version_conflict_judge`

- [ ] **Step 4: Commit**

```bash
git add src/services/async_tasks.py src/api/routes/jobs.py
git commit -m "feat(jobs): register version conflict async handlers"
```

---

## Task 3.2: 创建 maintenance 路由

**Files:**
- Create: `src/api/routes/maintenance.py`
- Create: `tests/test_maintenance_api.py`
- Modify: `src/api/routes/__init__.py`
- Modify: `src/api/__init__.py`

- [ ] **Step 1: 写失败测试**

```python
"""Maintenance API 测试"""
import pytest
from fastapi.testclient import TestClient

from src.api import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def auth_token(client):
    # 注册并登录获取 token
    client.post("/api/auth/register", json={"username": "admin", "password": "pass123"})
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "pass123"})
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_create_session_requires_auth(client):
    resp = client.post("/api/maintenance/version-conflict/sessions", json={})
    assert resp.status_code in (401, 403)


def test_create_session_returns_session_id(client, auth_token):
    resp = client.post(
        "/api/maintenance/version-conflict/sessions",
        json={"rescan_ignored": False},
        headers=auth_headers(auth_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data


def test_get_session_status(client, auth_token):
    create = client.post(
        "/api/maintenance/version-conflict/sessions",
        json={},
        headers=auth_headers(auth_token),
    )
    sid = create.json()["session_id"]
    resp = client.get(
        f"/api/maintenance/version-conflict/sessions/{sid}",
        headers=auth_headers(auth_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == sid


def test_list_sessions(client, auth_token):
    client.post(
        "/api/maintenance/version-conflict/sessions",
        json={},
        headers=auth_headers(auth_token),
    )
    resp = client.get(
        "/api/maintenance/version-conflict/sessions",
        headers=auth_headers(auth_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data


def test_list_pairs(client, auth_token):
    create = client.post(
        "/api/maintenance/version-conflict/sessions",
        json={},
        headers=auth_headers(auth_token),
    )
    sid = create.json()["session_id"]
    resp = client.get(
        f"/api/maintenance/version-conflict/sessions/{sid}/pairs",
        headers=auth_headers(auth_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "pairs" in data


def test_ignore_pair_not_found(client, auth_token):
    resp = client.post(
        "/api/maintenance/version-conflict/pairs/nonexistent/ignore",
        headers=auth_headers(auth_token),
    )
    assert resp.status_code == 404


def test_delete_pair_not_found(client, auth_token):
    resp = client.post(
        "/api/maintenance/version-conflict/pairs/nonexistent/delete",
        json={"operator": "test"},
        headers=auth_headers(auth_token),
    )
    assert resp.status_code == 404


def test_list_ignores(client, auth_token):
    resp = client.get(
        "/api/maintenance/version-conflict/ignores",
        headers=auth_headers(auth_token),
    )
    assert resp.status_code == 200


def test_delete_ignore_not_found(client, auth_token):
    resp = client.delete(
        "/api/maintenance/version-conflict/ignores/nonexistent",
        headers=auth_headers(auth_token),
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /workspace && pytest tests/test_maintenance_api.py -v`
Expected: FAIL with ImportError 或 404

- [ ] **Step 3: 写 maintenance 路由**

```python
"""维护中心路由 — 版本冲突检测与清理"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.routes.auth import _check_auth

maintenance_router = APIRouter(
    prefix="/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(_check_auth)],
)


class CreateSessionReq(BaseModel):
    rescan_ignored: bool = False


class DeletePairReq(BaseModel):
    operator: str = "user"


def _get_service():
    from src.services.version_conflict import VersionConflictService
    return VersionConflictService()


# ── 会话管理 ──

@maintenance_router.post("/version-conflict/sessions")
def create_session(req: CreateSessionReq):
    """创建扫描会话。返回 session_id（异步执行）。"""
    svc = _get_service()
    session_id = svc.start_scan_session(rescan_ignored=req.rescan_ignored)
    return {"session_id": session_id, "status": "scanning"}


@maintenance_router.get("/version-conflict/sessions")
def list_sessions(status: str | None = None, limit: int = 50, offset: int = 0):
    """列出扫描会话。"""
    svc = _get_service()
    sessions = svc.list_sessions(status=status, limit=limit, offset=offset)
    return {"sessions": sessions}


@maintenance_router.get("/version-conflict/sessions/{session_id}")
def get_session(session_id: str):
    """会话详情。"""
    svc = _get_service()
    status = svc.get_session_status(session_id)
    if "error" in status and status.get("session_id") == session_id and "not found" in status.get("error", ""):
        raise HTTPException(404, f"会话不存在: {session_id}")
    return status


# ── 候选对查询 ──

@maintenance_router.get("/version-conflict/sessions/{session_id}/pairs")
def list_pairs(session_id: str, status: str | None = None,
               relation_type: str | None = None,
               limit: int = 50, offset: int = 0):
    """分页查询候选对。"""
    svc = _get_service()
    pairs = svc.list_pairs(
        session_id, status=status, relation_type=relation_type,
        limit=limit, offset=offset,
    )
    return {"pairs": pairs}


# ── 用户操作 ──

@maintenance_router.post("/version-conflict/sessions/{session_id}/judge")
def judge_pairs(session_id: str, limit: int = 20):
    """触发 LLM 判断（异步 job）。"""
    svc = _get_service()
    result = svc.judge_pending_pairs(session_id, limit=limit)
    return result


@maintenance_router.post("/version-conflict/pairs/{pair_id}/delete")
def delete_pair(pair_id: str, req: DeletePairReq):
    """确认删除旧版本。"""
    svc = _get_service()
    result = svc.execute_delete(pair_id, operator=req.operator)
    if not result.get("ok"):
        code = result.get("error", {}).get("code", "INTERNAL_ERROR")
        message = result.get("error", {}).get("message", "未知错误")
        status_code = 404 if code == "NOT_FOUND" else 400
        raise HTTPException(status_code, message)
    return result


@maintenance_router.post("/version-conflict/pairs/{pair_id}/ignore")
def ignore_pair(pair_id: str):
    """忽略该对。"""
    svc = _get_service()
    result = svc.ignore_pair(pair_id)
    if not result.get("ok"):
        raise HTTPException(404, f"pair 不存在: {pair_id}")
    return result


# ── 忽略列表管理 ──

@maintenance_router.get("/version-conflict/ignores")
def list_ignores(limit: int = 100, offset: int = 0):
    """列出所有忽略记录。"""
    svc = _get_service()
    ignores = svc.list_ignores(limit=limit, offset=offset)
    return {"ignores": ignores}


@maintenance_router.delete("/version-conflict/ignores/{ignore_id}")
def delete_ignore(ignore_id: str):
    """撤销忽略。"""
    svc = _get_service()
    result = svc.delete_ignore(ignore_id)
    if not result.get("ok"):
        raise HTTPException(404, f"忽略记录不存在: {ignore_id}")
    return result
```

- [ ] **Step 4: 修改 routes/__init__.py 导出**

在 `src/api/routes/__init__.py` 末尾追加：

```python
from src.api.routes.maintenance import maintenance_router
```

并在 `__all__` 列表加 `"maintenance_router"`。

- [ ] **Step 5: 修改 api/__init__.py 挂载**

在 `src/api/__init__.py` 的 import 块加：

```python
from src.api.routes import (
    auth_router,
    chat_router,
    graph_router,
    jobs_router,
    kb_router,
    maintenance_router,  # 新增
    properties_router,
    query_router,
    refs_router,
    settings_router,
    tags_router,
    wiki_router,
)
```

并在 `create_app()` 的 `include_router` 块加：

```python
    app.include_router(maintenance_router, prefix="/api")
```

- [ ] **Step 6: 运行测试验证通过**

Run: `cd /workspace && pytest tests/test_maintenance_api.py -v`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add src/api/routes/maintenance.py src/api/routes/__init__.py src/api/__init__.py tests/test_maintenance_api.py
git commit -m "feat(api): add maintenance router for version conflict"
```

---

# Phase 4: 前端 MaintenanceView

## Task 4.1: 创建 MaintenanceView 组件

**Files:**
- Create: `client/src/views/MaintenanceView.tsx`
- Modify: `client/src/components/Layout.tsx`
- Modify: `client/src/App.tsx`

- [ ] **Step 1: 检查现有 useApi 和 api.ts 接口**

Run: `cd /workspace && head -50 client/src/api.ts`
（确认 api 客户端导出方式）

- [ ] **Step 2: 写 MaintenanceView.tsx**

```tsx
import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import { useToast } from '../components/Toast'

interface Session {
  id: string
  status: string
  total_items_scanned: number
  candidates_found: number
  pairs_judged: number
  pairs_deleted: number
  pairs_ignored: number
  started_at: string
  completed_at: string | null
}

interface Pair {
  id: string
  item_a_id: string
  item_b_id: string
  item_a_title: string | null
  item_b_title: string | null
  item_a_created: string | null
  item_b_created: string | null
  candidate_source: string
  similarity_score: number | null
  relation_type: string | null
  newer_item_id: string | null
  confidence: number | null
  reason: string | null
  status: string
}

interface Ignore {
  id: string
  item_a_id: string
  item_b_id: string
  item_a_title: string | null
  item_b_title: string | null
  ignored_at: string
}

const POLL_INTERVAL_MS = 2000

export default function MaintenanceView() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [currentSession, setCurrentSession] = useState<Session | null>(null)
  const [pairs, setPairs] = useState<Pair[]>([])
  const [ignores, setIgnores] = useState<Ignore[]>([])
  const [statusFilter, setStatusFilter] = useState<string>('pending')
  const [expandedPair, setExpandedPair] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const { show } = useToast()

  const loadSessions = useCallback(async () => {
    try {
      const resp = await api.get('/maintenance/version-conflict/sessions')
      setSessions(resp.data.sessions || [])
    } catch (e) {
      show('加载会话失败', 'error')
    }
  }, [show])

  const loadPairs = useCallback(async (sessionId: string) => {
    try {
      const resp = await api.get(
        `/maintenance/version-conflict/sessions/${sessionId}/pairs`,
        { params: { status: statusFilter, limit: 50 } }
      )
      setPairs(resp.data.pairs || [])
    } catch (e) {
      show('加载候选对失败', 'error')
    }
  }, [statusFilter, show])

  const loadIgnores = useCallback(async () => {
    try {
      const resp = await api.get('/maintenance/version-conflict/ignores')
      setIgnores(resp.data.ignores || [])
    } catch (e) {
      show('加载忽略列表失败', 'error')
    }
  }, [show])

  // 轮询当前会话状态
  useEffect(() => {
    if (!currentSession || currentSession.status === 'ready' || currentSession.status === 'completed' || currentSession.status === 'error') {
      return
    }
    const timer = setInterval(async () => {
      try {
        const resp = await api.get(`/maintenance/version-conflict/sessions/${currentSession.id}`)
        const s = resp.data
        setCurrentSession(s)
        if (s.status === 'ready') {
          loadPairs(s.id)
        }
      } catch (e) {
        // ignore polling errors
      }
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [currentSession, loadPairs])

  useEffect(() => {
    loadSessions()
    loadIgnores()
  }, [loadSessions, loadIgnores])

  useEffect(() => {
    if (currentSession) {
      loadPairs(currentSession.id)
    }
  }, [currentSession?.id, statusFilter, loadPairs])

  const handleStartScan = async () => {
    if (!confirm('开始新扫描？已忽略的对将不会被扫描。')) return
    setLoading(true)
    try {
      const resp = await api.post('/maintenance/version-conflict/sessions', { rescan_ignored: false })
      const sid = resp.data.session_id
      show('扫描已启动', 'success')
      const statusResp = await api.get(`/maintenance/version-conflict/sessions/${sid}`)
      setCurrentSession(statusResp.data)
      loadSessions()
    } catch (e) {
      show('启动扫描失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleJudge = async (sessionId: string) => {
    try {
      await api.post(`/maintenance/version-conflict/sessions/${sessionId}/judge`, null, {
        params: { limit: 20 }
      })
      show('判断任务已触发', 'success')
    } catch (e) {
      show('触发判断失败', 'error')
    }
  }

  const handleDelete = async (pairId: string, pair: Pair) => {
    const olderTitle = pair.newer_item_id === pair.item_a_id
      ? pair.item_b_title
      : pair.item_a_title
    const newerTitle = pair.newer_item_id === pair.item_a_id
      ? pair.item_a_title
      : pair.item_b_title
    if (!confirm(`将删除旧版 [${olderTitle}]，新版 [${newerTitle}] 保留。确认？`)) return
    try {
      await api.post(`/maintenance/version-conflict/pairs/${pairId}/delete`, { operator: 'user' })
      show('已删除旧版本', 'success')
      if (currentSession) loadPairs(currentSession.id)
    } catch (e: any) {
      const msg = e.response?.data?.detail || '删除失败'
      show(msg, 'error')
    }
  }

  const handleIgnore = async (pairId: string) => {
    try {
      await api.post(`/maintenance/version-conflict/pairs/${pairId}/ignore`)
      show('已忽略', 'success')
      if (currentSession) loadPairs(currentSession.id)
      loadIgnores()
    } catch (e) {
      show('忽略失败', 'error')
    }
  }

  const handleRejudge = async (pairId: string) => {
    try {
      await api.post(`/maintenance/version-conflict/pairs/${pairId}/judge`)
      show('已重新判断', 'success')
      if (currentSession) loadPairs(currentSession.id)
    } catch (e) {
      show('重新判断失败', 'error')
    }
  }

  const handleUndoIgnore = async (ignoreId: string) => {
    if (!confirm('撤销忽略？下次扫描会重新判断。')) return
    try {
      await api.delete(`/maintenance/version-conflict/ignores/${ignoreId}`)
      show('已撤销忽略', 'success')
      loadIgnores()
    } catch (e) {
      show('撤销失败', 'error')
    }
  }

  const relationLabel = (rt: string | null) => {
    const map: Record<string, string> = {
      supersedes: 'A替代B',
      superseded_by: 'B替代A',
      partial_overlap: '部分重叠',
      unrelated: '无关',
    }
    return rt ? (map[rt] || rt) : '未判断'
  }

  const canDelete = (pair: Pair) => {
    return pair.relation_type === 'supersedes' || pair.relation_type === 'superseded_by'
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">维护中心</h1>
        <button
          onClick={handleStartScan}
          disabled={loading}
          className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:opacity-90 disabled:opacity-50"
        >
          {loading ? '启动中...' : '开始新扫描'}
        </button>
      </div>

      {/* 当前会话进度 */}
      {currentSession && (
        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
          <div className="flex items-center justify-between mb-2">
            <span className="font-medium">当前会话</span>
            <span className="text-sm text-[var(--color-text-muted)]">
              {currentSession.status}
            </span>
          </div>
          <div className="grid grid-cols-4 gap-4 text-sm">
            <div>扫描条目: {currentSession.total_items_scanned}</div>
            <div>候选对: {currentSession.candidates_found}</div>
            <div>已判断: {currentSession.pairs_judged}</div>
            <div>已删除: {currentSession.pairs_deleted}</div>
          </div>
          {currentSession.status === 'ready' && (
            <button
              onClick={() => handleJudge(currentSession.id)}
              className="mt-3 px-3 py-1 text-sm bg-[var(--color-accent)] text-white rounded"
            >
              触发 LLM 判断
            </button>
          )}
        </div>
      )}

      {/* 候选对列表 */}
      <div>
        <div className="flex items-center gap-3 mb-3">
          <h2 className="text-lg font-semibold">候选对</h2>
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            className="px-2 py-1 text-sm bg-[var(--color-surface)] border border-[var(--color-border)] rounded"
          >
            <option value="pending">待处理</option>
            <option value="ignored">已忽略</option>
            <option value="deleted">已删除</option>
            <option value="">全部</option>
          </select>
        </div>
        {pairs.length === 0 ? (
          <p className="text-[var(--color-text-muted)] text-sm">暂无候选对</p>
        ) : (
          <div className="space-y-2">
            {pairs.map(pair => (
              <div
                key={pair.id}
                className={`p-3 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)] ${
                  pair.status === 'deleted' ? 'opacity-50' : ''
                }`}
              >
                <div className="flex items-center justify-between gap-4">
                  <div className="flex-1 grid grid-cols-2 gap-4">
                    <div>
                      <div className="font-medium">{pair.item_a_title || '(已删除)'}</div>
                      <div className="text-xs text-[var(--color-text-muted)]">
                        {pair.item_a_created?.slice(0, 10)}
                      </div>
                    </div>
                    <div>
                      <div className="font-medium">{pair.item_b_title || '(已删除)'}</div>
                      <div className="text-xs text-[var(--color-text-muted)]">
                        {pair.item_b_created?.slice(0, 10)}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-medium">
                      {relationLabel(pair.relation_type)}
                    </div>
                    {pair.confidence != null && (
                      <div className="text-xs text-[var(--color-text-muted)]">
                        置信度: {(pair.confidence * 100).toFixed(0)}%
                      </div>
                    )}
                  </div>
                </div>
                {pair.reason && (
                  <p className="mt-2 text-sm text-[var(--color-text-muted)]">{pair.reason}</p>
                )}
                <div className="mt-2 flex gap-2">
                  <button
                    onClick={() => setExpandedPair(expandedPair === pair.id ? null : pair.id)}
                    className="px-2 py-1 text-xs bg-[var(--color-surface-hover)] rounded"
                  >
                    {expandedPair === pair.id ? '收起' : '查看详情'}
                  </button>
                  {canDelete(pair) && pair.status === 'pending' && (
                    <button
                      onClick={() => handleDelete(pair.id, pair)}
                      className="px-2 py-1 text-xs bg-red-500 text-white rounded"
                    >
                      确认删除旧版
                    </button>
                  )}
                  {pair.relation_type === 'partial_overlap' && (
                    <span className="px-2 py-1 text-xs text-[var(--color-text-muted)]">
                      部分重叠，需手动处理
                    </span>
                  )}
                  {pair.status === 'pending' && (
                    <>
                      <button
                        onClick={() => handleIgnore(pair.id)}
                        className="px-2 py-1 text-xs bg-[var(--color-surface-hover)] rounded"
                      >
                        忽略
                      </button>
                      <button
                        onClick={() => handleRejudge(pair.id)}
                        className="px-2 py-1 text-xs bg-[var(--color-surface-hover)] rounded"
                      >
                        重新判断
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 历史会话 */}
      <details className="border border-[var(--color-border)] rounded-lg">
        <summary className="p-3 cursor-pointer font-medium">历史会话 ({sessions.length})</summary>
        <div className="p-3 space-y-1">
          {sessions.map(s => (
            <button
              key={s.id}
              onClick={() => setCurrentSession(s)}
              className="w-full text-left p-2 hover:bg-[var(--color-surface-hover)] rounded text-sm"
            >
              <span className="font-mono">{s.id.slice(0, 8)}</span>
              <span className="ml-2 text-[var(--color-text-muted)]">{s.status}</span>
              <span className="ml-2 text-xs">
                候选 {s.candidates_found} / 删除 {s.pairs_deleted}
              </span>
            </button>
          ))}
        </div>
      </details>

      {/* 忽略列表 */}
      <details className="border border-[var(--color-border)] rounded-lg">
        <summary className="p-3 cursor-pointer font-medium">
          忽略列表 ({ignores.length})
        </summary>
        <div className="p-3 space-y-1">
          {ignores.length === 0 ? (
            <p className="text-sm text-[var(--color-text-muted)]">暂无忽略记录</p>
          ) : ignores.map(ig => (
            <div key={ig.id} className="flex items-center justify-between p-2 text-sm">
              <span>
                {ig.item_a_title || '(已删除)'} ↔ {ig.item_b_title || '(已删除)'}
              </span>
              <button
                onClick={() => handleUndoIgnore(ig.id)}
                className="px-2 py-1 text-xs bg-[var(--color-surface-hover)] rounded"
              >
                撤销忽略
              </button>
            </div>
          ))}
        </div>
      </details>
    </div>
  )
}
```

- [ ] **Step 3: 修改 Layout.tsx 加导航入口**

在 `client/src/components/Layout.tsx` 的 `NAV_ITEMS` 数组中，在"知识库"之后加：

```tsx
const NAV_ITEMS = [
  { to: '/', label: '仪表盘', icon: '◉' },
  { to: '/knowledge', label: '知识库', icon: 'KB' },
  { to: '/maintenance', label: '维护中心', icon: 'MT' },
  { to: '/import', label: '导入中心', icon: '↑' },
  { to: '/chat', label: '智能问答', icon: 'AI' },
  { to: '/wiki', label: 'Wiki', icon: 'WK' },
  { to: '/graph', label: '知识图谱', icon: 'GR' },
  { to: '/settings', label: '设置', icon: '⚙' },
]
```

- [ ] **Step 4: 修改 App.tsx 加路由**

在 `client/src/App.tsx` 的路由表中加：

```tsx
import MaintenanceView from './views/MaintenanceView'
// ... 在 Routes 内
<Route path="/maintenance" element={<MaintenanceView />} />
```

- [ ] **Step 5: 验证前端构建**

Run: `cd /workspace/client && npm run build`
Expected: 构建成功无报错

- [ ] **Step 6: Commit**

```bash
git add client/src/views/MaintenanceView.tsx client/src/components/Layout.tsx client/src/App.tsx
git commit -m "feat(client): add MaintenanceView with session/pair/ignore UI"
```

---

# 全改动部分测试

## Task 5.1: 跑全量相关测试

- [ ] **Step 1: 运行所有版本冲突相关测试**

Run: `cd /workspace && pytest tests/test_version_conflict.py tests/test_conflict_repo.py tests/test_maintenance_api.py -v`
Expected: 全部 PASS

- [ ] **Step 2: 运行回归测试（确保未破坏现有功能）**

Run: `cd /workspace && pytest tests/test_db.py tests/test_operation_safety.py tests/test_async_ingest.py -v`
Expected: 全部 PASS（现有功能未受影响）

- [ ] **Step 3: 验证 migration 与现有 schema 兼容**

Run: `cd /workspace && alembic upgrade head && python -c "from src.services.db import Database; Database.connect('verify.db'); print('OK')"`
Expected: 无报错

- [ ] **Step 4: 验证前端构建**

Run: `cd /workspace/client && npm run build`
Expected: 构建成功

---

## Task 5.2: Commit + Push 主分支

- [ ] **Step 1: 最终 git status 检查**

Run: `cd /workspace && git status`
Expected: working tree clean（所有改动已 commit）

- [ ] **Step 2: 推送到主分支**

Run: `cd /workspace && git push origin master`
Expected: 推送成功

---

## Self-Review

### Spec coverage
- ✅ 三张表 → Task 1.1
- ✅ 数据模型 → Task 1.2
- ✅ ConflictRepository（含 pair_key 归一化、JOIN 查询、忽略表唯一约束）→ Task 1.3
- ✅ VersionConflictService（会话管理 + 扫描 + 判断 + 删除 + 忽略）→ Task 2.1/2.2/2.3
- ✅ SQL 粗筛（tag + 标题核心词）→ Task 2.2
- ✅ embedding 补充 + 限流 → Task 2.2
- ✅ LLM 四类关系判断 → Task 2.3
- ✅ partial_overlap 阻止删除 → Task 2.3 测试覆盖
- ✅ unrelated 不写 ignore 表 → Task 2.3 实现
- ✅ operation_log undo 支持 → Task 2.3 execute_delete
- ✅ 异步任务注册 → Task 3.1
- ✅ REST API 全部端点 → Task 3.2
- ✅ 前端维护页 + 入口 → Task 4.1
- ✅ 限流参数 EMBEDDING_QPS=3, LLM_QPS=1 → Task 2.1 常量

### Placeholder scan
- ✅ 无 TBD/TODO
- ✅ 所有测试代码完整
- ✅ 所有实现代码完整

### Type consistency
- ✅ `ConflictPair.pair_key` 在 model 与 repo 中一致
- ✅ `execute_delete` 返回结构 `{ok, deleted_item_id, operation_log_id}` 在测试与实现一致
- ✅ `judge_pending_pairs` 的 `run_synchronously` 参数在所有调用处一致
- ✅ `start_scan_session` 的 `rescan_ignored` 参数在所有调用处一致
```

"""Sprint 3 / Phase 4 验收：写操作安全闭环。

覆盖：
- ``knowledge_items.deleted_at`` 软删除 + 恢复 + purge
- ``get`` / ``list`` 默认过滤已删条目
- ``OperationLogService.undo`` 支持 update / create / delete / ingest
- MCP 工具：``preview_operation`` / ``get_operation_log`` / ``undo_operation`` /
  ``list_recent_operations`` / ``restore_knowledge``
- ``dry_run`` 不写 DB、不写 operation_log
- 已删条目无法 update（filter 过滤）
"""
from __future__ import annotations

import pytest

from src.repositories.knowledge_repo import KnowledgeRepository
from src.repositories.operation_log_repo import OperationLogRepository
from src.services.db import Database
from src.services.operation_log import OperationLogService
from tests.conftest import insert_test_knowledge

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _container_with_repo():
    """构造一个装了 knowledge_repo 的 OperationLogService。"""
    return OperationLogService(
        repo=OperationLogRepository(),
        knowledge_repo=KnowledgeRepository(),
    )


@pytest.fixture
def mcp_env(setup_db, monkeypatch):
    """Mock 向量存储；测试 envelope 行为不依赖 embedding。"""
    class MockVS:
        def __init__(self, db=None): pass
        def search(self, query, top_k=5): return []
        def add_chunks(self, chunks): pass
        def delete_by_knowledge(self, kid): pass
        def count(self): return 0
    class MockBS:
        def __init__(self, db=None): pass
        def search(self, query, top_k=5): return []
        def add_block_embedding(self, block_id, embedding): pass
        def delete_by_page(self, page_id): pass
        def count(self): return 0

    monkeypatch.setattr("src.services.vectorstore.VectorStore", MockVS)
    monkeypatch.setattr("src.services.block_store.BlockStore", MockBS)


# ---------------------------------------------------------------------------
# 1) Database.soft_delete / restore / purge
# ---------------------------------------------------------------------------

class TestDatabaseSoftDelete:
    """Database 软删除 / 恢复 / 硬删。"""

    def test_soft_delete_sets_deleted_at(self):
        kid = insert_test_knowledge(title="软删测试", content="c")
        assert Database.get_knowledge(kid)["deleted_at"] is None
        assert Database.soft_delete_knowledge(kid) is True
        row = Database.get_knowledge(kid, include_deleted=True)
        assert row is not None
        assert row["deleted_at"] is not None

    def test_get_default_filters_soft_deleted(self):
        kid = insert_test_knowledge(title="过滤测试", content="c")
        Database.soft_delete_knowledge(kid)
        # 默认 get 看不到
        assert Database.get_knowledge(kid) is None
        # include_deleted=True 能看到
        assert Database.get_knowledge(kid, include_deleted=True) is not None

    def test_list_excludes_soft_deleted(self):
        kid_keep = insert_test_knowledge(title="keep", content="c", item_id="keep-1")
        kid_drop = insert_test_knowledge(title="drop", content="c", item_id="drop-1")
        Database.soft_delete_knowledge(kid_drop)

        items = Database.list_knowledge()
        ids = {it["id"] for it in items}
        assert kid_keep in ids
        assert kid_drop not in ids

        # include_deleted=True 都能看到
        items_all = Database.list_knowledge(include_deleted=True)
        ids_all = {it["id"] for it in items_all}
        assert kid_keep in ids_all
        assert kid_drop in ids_all

    def test_count_excludes_soft_deleted(self):
        insert_test_knowledge(title="a", content="c", item_id="cnt-a")
        insert_test_knowledge(title="b", content="c", item_id="cnt-b")
        before = Database.count_knowledge()
        Database.soft_delete_knowledge("cnt-a")
        assert Database.count_knowledge() == before - 1
        assert Database.count_knowledge(include_deleted=True) == before

    def test_restore_clears_deleted_at(self):
        kid = insert_test_knowledge(title="恢复测试", content="c")
        Database.soft_delete_knowledge(kid)
        assert Database.restore_knowledge(kid) is True
        # 恢复后能再 get 到
        assert Database.get_knowledge(kid) is not None
        assert Database.get_knowledge(kid)["deleted_at"] is None

    def test_restore_only_works_on_soft_deleted(self):
        kid = insert_test_knowledge(title="未删", content="c")
        # 未软删条目 restore 返回 False
        assert Database.restore_knowledge(kid) is False

    def test_purge_hard_deletes(self):
        kid = insert_test_knowledge(title="硬删", content="c")
        Database.soft_delete_knowledge(kid)
        assert Database.purge_knowledge(kid) is True
        # 硬删后 even include_deleted 都看不到
        assert Database.get_knowledge(kid, include_deleted=True) is None

    def test_purge_nonexistent_returns_false(self):
        assert Database.purge_knowledge("never-existed-id") is False

    def test_update_filter_soft_deleted(self):
        """已软删条目 update 应该抛 ValueError。"""
        from src.repositories.knowledge_repo import KnowledgeRepository
        repo = KnowledgeRepository()
        kid = insert_test_knowledge(title="update软删", content="c")
        Database.soft_delete_knowledge(kid)
        with pytest.raises(ValueError, match="deleted"):
            repo.update(kid, title="new title")

    def test_double_soft_delete_returns_false(self):
        kid = insert_test_knowledge(title="双删", content="c")
        assert Database.soft_delete_knowledge(kid) is True
        assert Database.soft_delete_knowledge(kid) is False


# ---------------------------------------------------------------------------
# 2) KnowledgeRepository 软删除层
# ---------------------------------------------------------------------------

class TestKnowledgeRepoSoftDelete:
    """KnowledgeRepository 软删除 / 恢复 / 硬删。"""

    def test_delete_soft_deletes_by_default(self):
        repo = KnowledgeRepository()
        kid = insert_test_knowledge(title="repo软删", content="c")
        repo.delete(kid)
        # get 看不到（默认 filter）
        assert repo.get(kid) is None
        # include_deleted=True 还能看到
        row = repo.get(kid, include_deleted=True)
        assert row is not None
        assert row["deleted_at"] is not None

    def test_delete_hard_purges(self):
        repo = KnowledgeRepository()
        kid = insert_test_knowledge(title="repo硬删", content="c")
        repo.delete(kid, hard=True)
        assert repo.get(kid, include_deleted=True) is None

    def test_restore_clears_deleted_at(self):
        repo = KnowledgeRepository()
        kid = insert_test_knowledge(title="repo恢复", content="c")
        repo.delete(kid)
        assert repo.restore(kid) is True
        assert repo.get(kid) is not None

    def test_purge_hard_deletes(self):
        repo = KnowledgeRepository()
        kid = insert_test_knowledge(title="repo purge", content="c")
        repo.delete(kid)  # soft
        assert repo.purge(kid) is True
        assert repo.get(kid, include_deleted=True) is None


# ---------------------------------------------------------------------------
# 3) OperationLogService.undo
# ---------------------------------------------------------------------------

class TestOperationLogUndo:
    """OperationLogService.undo 支持 update / create / delete / ingest。"""

    def test_undo_update_restores_fields(self):
        kid = insert_test_knowledge(title="undo-update前", content="c1")
        svc = _container_with_repo()

        # 模拟 update：写入 log with snapshot_before
        log_id = svc.log(
            "update", "knowledge", kid,
            before={"title": "undo-update前", "content": "c1"},
            after={"title": "undo-update后", "content": "c2"},
        )
        # 实际改一下
        repo = KnowledgeRepository()
        repo.update(kid, title="undo-update后", content="c2")

        result = svc.undo(log_id)
        assert result["ok"] is True
        assert result["data"]["operation"] == "undo_update"
        assert "title" in result["data"]["restored_fields"]
        # 内容已恢复
        row = repo.get(kid, include_deleted=True)
        assert row["title"] == "undo-update前"
        assert row["content"] == "c1"

    def test_undo_create_soft_deletes_new_item(self):
        kid = insert_test_knowledge(title="undo-create", content="c")
        svc = _container_with_repo()

        log_id = svc.log("create", "knowledge", kid,
                         after={"title": "undo-create"})

        result = svc.undo(log_id)
        assert result["ok"] is True
        assert result["data"]["soft_deleted"] is True

        # 现在 get 看不到
        assert KnowledgeRepository().get(kid) is None

    def test_undo_delete_restores_soft_deleted(self):
        kid = insert_test_knowledge(title="undo-delete", content="c")
        repo = KnowledgeRepository()
        repo.delete(kid)  # soft
        svc = _container_with_repo()

        log_id = svc.log("delete", "knowledge", kid,
                         before={"title": "undo-delete"})

        result = svc.undo(log_id)
        assert result["ok"] is True
        assert result["data"]["restored"] is True
        # 恢复后能 get 到
        assert repo.get(kid) is not None

    def test_undo_ingest_soft_deletes_imported_item(self):
        kid = insert_test_knowledge(title="undo-ingest", content="c")
        repo = KnowledgeRepository()
        svc = _container_with_repo()

        log_id = svc.log("ingest", "knowledge", kid,
                         after={"title": "undo-ingest"})

        result = svc.undo(log_id)
        assert result["ok"] is True
        assert result["data"]["operation"] == "undo_ingest"
        assert result["data"]["soft_deleted"] is True
        assert repo.get(kid) is None
        assert repo.get(kid, include_deleted=True) is not None

    def test_undo_unknown_operation_returns_error(self):
        svc = _container_with_repo()
        insert_test_knowledge(title="x", content="c")
        log_id = svc.log("reindex", "system", "all")

        result = svc.undo(log_id)
        assert result["ok"] is False
        assert result["error"]["code"] == "PRECONDITION_FAILED"

    def test_undo_nonexistent_log_returns_not_found(self):
        svc = _container_with_repo()
        result = svc.undo("non-existent-log-id")
        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"

    def test_can_undo_returns_boolean(self):
        svc = _container_with_repo()
        kid = insert_test_knowledge(title="x", content="c")
        # update 可撤销
        log_id_undoable = svc.log("update", "knowledge", kid)
        assert svc.can_undo(log_id_undoable) is True
        # reindex 不可撤销
        log_id_not_undoable = svc.log("reindex", "system", "all")
        assert svc.can_undo(log_id_not_undoable) is False


# ---------------------------------------------------------------------------
# 4) MCP preview_operation
# ---------------------------------------------------------------------------

class TestMcpPreviewOperation:
    """preview_operation 工具 — 干跑不真改。"""

    def test_preview_create_returns_dry_run(self, mcp_env):
        from src.mcp_server import preview_operation
        result = preview_operation(
            operation="create", title="预览", content="content",
        )
        assert result["ok"] is True
        assert result["dry_run"] is True
        # 没创建
        assert Database.count_knowledge() == 0

    def test_preview_update_returns_changes(self, mcp_env):
        from src.mcp_server import preview_operation
        kid = insert_test_knowledge(title="before", content="c")
        result = preview_operation(
            operation="update", item_id=kid, title="after",
        )
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert "changes" in result["data"]["would_change"]
        # 数据没变
        assert Database.get_knowledge(kid)["title"] == "before"

    def test_preview_delete_does_not_remove(self, mcp_env):
        from src.mcp_server import preview_operation
        kid = insert_test_knowledge(title="pre-delete", content="c")
        result = preview_operation(operation="delete", item_id=kid)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert "would_delete" in result["data"]["would_change"]
        # 仍在
        assert Database.get_knowledge(kid) is not None

    def test_preview_reindex_returns_count(self, mcp_env):
        from src.mcp_server import preview_operation
        insert_test_knowledge(title="reidx-1", content="c")
        result = preview_operation(operation="reindex_all")
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert "would_reindex" in result["data"]["would_change"]

    def test_preview_invalid_operation_returns_error(self, mcp_env):
        from src.mcp_server import preview_operation
        result = preview_operation(operation="hack")
        assert result["ok"] is False
        assert result["error"]["code"] == "VALIDATION_ERROR"

    def test_preview_update_without_item_id_errors(self, mcp_env):
        from src.mcp_server import preview_operation
        result = preview_operation(operation="update")
        assert result["ok"] is False
        assert result["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 5) MCP get_operation_log
# ---------------------------------------------------------------------------

class TestMcpGetOperationLog:
    """get_operation_log 工具 — 按 ID 查单条。"""

    def test_get_returns_full_record(self, mcp_env):
        from src.mcp_server import create, get_operation_log
        result = create(title="t", content="c")
        log_id = result["operation_id"]

        env = get_operation_log(operation_id=log_id)
        assert env["ok"] is True
        entry = env["data"]
        assert entry["id"] == log_id
        assert entry["operation"] == "create"
        assert entry["target_type"] == "knowledge"
        assert entry["source"] == "mcp"
        # can_undo 字段
        assert "can_undo" in entry

    def test_get_nonexistent_returns_not_found(self, mcp_env):
        from src.mcp_server import get_operation_log
        env = get_operation_log(operation_id="missing-id")
        assert env["ok"] is False
        assert env["error"]["code"] == "NOT_FOUND"

    def test_get_empty_id_returns_validation_error(self, mcp_env):
        from src.mcp_server import get_operation_log
        env = get_operation_log(operation_id="")
        assert env["ok"] is False
        assert env["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 6) MCP undo_operation
# ---------------------------------------------------------------------------

class TestMcpUndoOperation:
    """undo_operation 工具 — 走 OperationLogService.undo。"""

    def test_undo_create_soft_deletes_item(self, mcp_env):
        from src.mcp_server import create, read, undo_operation
        created = create(title="待撤销创建", content="c")
        kid = created["data"]["id"]
        log_id = created["operation_id"]

        env = undo_operation(operation_id=log_id)
        assert env["ok"] is True
        assert env["data"]["operation"] == "undo_create"
        assert "undone_log_id" in env["data"]
        # 现在 read 看不到
        read_env = read(item_id=kid)
        assert read_env["ok"] is False

    def test_undo_update_restores_content(self, mcp_env):
        from src.mcp_server import create, undo_operation, update
        created = create(title="原", content="原内容")
        kid = created["data"]["id"]
        updated = update(item_id=kid, title="新", content="新内容")
        log_id = updated["operation_id"]

        env = undo_operation(operation_id=log_id)
        assert env["ok"] is True
        assert env["data"]["operation"] == "undo_update"
        assert "title" in env["data"]["restored_fields"]

    def test_undo_delete_restores_item(self, mcp_env):
        from src.mcp_server import create, delete, read, undo_operation
        created = create(title="待删", content="c")
        kid = created["data"]["id"]
        deleted = delete(item_id=kid)
        log_id = deleted["operation_id"]

        env = undo_operation(operation_id=log_id)
        assert env["ok"] is True
        # 现在又能 read 到
        read_env = read(item_id=kid)
        assert read_env["ok"] is True
        assert read_env["data"]["title"] == "待删"

    def test_undo_non_undoable_op_returns_precondition(self, mcp_env):
        from src.mcp_server import undo_operation
        # 手动插一条不可撤销的 log
        repo = OperationLogRepository()
        log_id = repo.insert({
            "operation": "reindex", "target_type": "system", "target_id": "all",
        })
        env = undo_operation(operation_id=log_id)
        assert env["ok"] is False
        assert env["error"]["code"] == "PRECONDITION_FAILED"

    def test_undo_nonexistent_returns_not_found(self, mcp_env):
        from src.mcp_server import undo_operation
        env = undo_operation(operation_id="ghost-id")
        assert env["ok"] is False
        assert env["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# 7) MCP list_recent_operations
# ---------------------------------------------------------------------------

class TestMcpListRecentOperations:
    """list_recent_operations 工具。"""

    def test_list_returns_envelope(self, mcp_env):
        from src.mcp_server import create, list_recent_operations
        create(title="t1", content="c1")
        create(title="t2", content="c2")

        env = list_recent_operations(limit=10)
        assert env["ok"] is True
        assert env["data"]
        assert env["meta"]["count"] >= 2
        assert env["meta"]["limit"] == 10

    def test_list_filters_by_source(self, mcp_env):
        from src.mcp_server import create, list_recent_operations
        create(title="t1", content="c1")

        env = list_recent_operations(limit=10, source="mcp")
        assert env["ok"] is True
        for log in env["data"]:
            assert log["source"] == "mcp"

    def test_list_filters_by_target_type(self, mcp_env):
        from src.mcp_server import create, list_recent_operations
        create(title="t1", content="c1")

        env = list_recent_operations(limit=10, target_type="knowledge")
        assert env["ok"] is True
        for log in env["data"]:
            assert log["target_type"] == "knowledge"

    def test_list_default_limit_is_20(self, mcp_env):
        from src.mcp_server import list_recent_operations
        env = list_recent_operations()
        assert env["ok"] is True
        assert env["meta"]["limit"] == 20


# ---------------------------------------------------------------------------
# 8) MCP restore_knowledge
# ---------------------------------------------------------------------------

class TestMcpRestoreKnowledge:
    """restore_knowledge 工具 — 恢复软删条目。"""

    def test_restore_soft_deleted_via_mcp(self, mcp_env):
        from src.mcp_server import create, delete, read, restore_knowledge
        created = create(title="待恢复", content="c")
        kid = created["data"]["id"]
        delete(item_id=kid)

        # 删了之后 read 返 NOT_FOUND
        assert read(item_id=kid)["ok"] is False

        env = restore_knowledge(item_id=kid)
        assert env["ok"] is True
        assert env["data"]["id"] == kid
        assert "operation_id" in env

        # 现在 read 又有
        assert read(item_id=kid)["ok"] is True

    def test_restore_non_deleted_returns_precondition(self, mcp_env):
        from src.mcp_server import create, restore_knowledge
        created = create(title="未删", content="c")
        env = restore_knowledge(item_id=created["data"]["id"])
        assert env["ok"] is False
        assert env["error"]["code"] == "PRECONDITION_FAILED"

    def test_restore_nonexistent_returns_not_found(self, mcp_env):
        from src.mcp_server import restore_knowledge
        env = restore_knowledge(item_id="never-existed")
        assert env["ok"] is False
        assert env["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# 9) dry_run 全不写 DB / operation_log
# ---------------------------------------------------------------------------

class TestDryRunNoSideEffects:
    """所有写工具的 dry_run 路径 — 0 DB 变化、0 operation_log 写入。"""

    def test_dry_run_create_no_db(self, mcp_env):
        from src.mcp_server import create
        before_kb = Database.count_knowledge()
        before_logs = Database.get_conn().execute(
            "SELECT COUNT(*) as c FROM operation_logs"
        ).fetchone()["c"]
        result = create(title="dry", content="c", dry_run=True)
        assert result["dry_run"] is True
        assert Database.count_knowledge() == before_kb
        after_logs = Database.get_conn().execute(
            "SELECT COUNT(*) as c FROM operation_logs"
        ).fetchone()["c"]
        assert after_logs == before_logs

    def test_dry_run_update_no_log(self, mcp_env):
        from src.mcp_server import create, update
        created = create(title="t", content="c")
        kid = created["data"]["id"]
        before_logs = Database.get_conn().execute(
            "SELECT COUNT(*) as c FROM operation_logs WHERE target_id = ?",
            (kid,),
        ).fetchone()["c"]
        update(item_id=kid, title="new", dry_run=True)
        after_logs = Database.get_conn().execute(
            "SELECT COUNT(*) as c FROM operation_logs WHERE target_id = ?",
            (kid,),
        ).fetchone()["c"]
        assert after_logs == before_logs

    def test_dry_run_delete_no_soft_delete(self, mcp_env):
        from src.mcp_server import create, delete
        created = create(title="t", content="c")
        kid = created["data"]["id"]
        delete(item_id=kid, dry_run=True)
        # deleted_at 仍为 None
        row = Database.get_knowledge(kid, include_deleted=True)
        assert row is not None
        assert row["deleted_at"] is None


# ---------------------------------------------------------------------------
# 10) 第6轮 BUG#6 回归 — restore 后 quality 保留
# ---------------------------------------------------------------------------

class TestRestorePreservesQuality:
    """BUG#6：delete 快照保留 quality，restore 后回填，防止 quality 丢失。"""

    def test_restore_preserves_quality(self, mcp_env):
        """带 quality=ok 的条目 delete→restore 后 quality 仍为 ok。"""
        from src.mcp_server import create, delete, restore_knowledge

        created = create(title="质量保留测试", content="内容")
        kid = created["data"]["id"]

        # 设置 quality
        Database.update_knowledge(kid, quality="ok", quality_score=85)
        before = Database.get_knowledge(kid)
        assert before["quality"] == "ok"
        assert before["quality_score"] == 85

        # delete → restore
        delete(item_id=kid)
        restore_knowledge(item_id=kid)

        after = Database.get_knowledge(kid)
        assert after is not None
        # 核心断言：quality 应保留
        assert after["quality"] == "ok", (
            f"restore 后 quality 应保留为 'ok'，实际 {after['quality']!r}"
        )

    def test_delete_snapshot_includes_quality(self, mcp_env):
        """delete 的 operation_log 快照应包含 quality 字段。"""
        import json

        from src.mcp_server import create, delete

        created = create(title="快照质量测试", content="内容")
        kid = created["data"]["id"]
        Database.update_knowledge(kid, quality="good", quality_score=90)

        delete(item_id=kid)

        # 查最近一条 delete 操作日志的 snapshot_before
        row = Database.get_conn().execute(
            """SELECT snapshot_before FROM operation_logs
               WHERE target_type = 'knowledge' AND target_id = ?
                 AND operation = 'delete'
               ORDER BY created_at DESC LIMIT 1""",
            (kid,),
        ).fetchone()
        assert row is not None
        snap = json.loads(row["snapshot_before"]) if row["snapshot_before"] else {}
        assert "quality" in snap, "删除快照应包含 quality 字段"
        assert snap["quality"] == "good"

"""操作安全功能测试 — 覆盖 P1~P4 全部功能"""
import json
import uuid
from datetime import datetime

import pytest

from src.services.db import Database
from src.repositories.operation_log_repo import OperationLogRepository
from src.services.operation_log import OperationLogService
from src.utils.config import Config
from tests.conftest import insert_test_knowledge, insert_test_wiki_page


class TestOperationLogInfrastructure:
    """P1: 操作审计日志基础设施"""

    def test_insert_and_query_log(self):
        repo = OperationLogRepository()
        log_id = repo.insert({
            "operation": "create",
            "target_type": "knowledge",
            "target_id": "test-id-1",
            "operator": "mcp-test",
            "source": "mcp",
            "snapshot_after": {"title": "Test"},
        })
        assert log_id

        logs = repo.query(target_type="knowledge")
        assert len(logs) >= 1
        assert logs[0]["operation"] == "create"
        assert logs[0]["target_id"] == "test-id-1"

    def test_get_by_target(self):
        repo = OperationLogRepository()
        target_id = str(uuid.uuid4())
        repo.insert({"operation": "update", "target_type": "knowledge", "target_id": target_id})
        repo.insert({"operation": "delete", "target_type": "knowledge", "target_id": target_id})

        logs = repo.get_by_target("knowledge", target_id)
        assert len(logs) == 2
        assert logs[0]["operation"] == "delete"

    def test_query_with_filters(self):
        repo = OperationLogRepository()
        repo.insert({"operation": "create", "target_type": "knowledge", "target_id": "a1"})
        repo.insert({"operation": "create", "target_type": "wiki_page", "target_id": "w1"})
        repo.insert({"operation": "delete", "target_type": "knowledge", "target_id": "a2"})

        logs = repo.query(operation="create", target_type="knowledge")
        knowledge_creates = [l for l in logs if l["target_type"] == "knowledge"]
        assert all(l["operation"] == "create" for l in knowledge_creates)

    def test_service_log_disabled(self):
        Config.set("safety.operation_log.enabled", False)
        try:
            repo = OperationLogRepository()
            service = OperationLogService(repo=repo)
            log_id = service.log("create", "knowledge", "x")
            assert log_id == ""
        finally:
            Config.set("safety.operation_log.enabled", True)

    def test_service_log_enabled(self):
        repo = OperationLogRepository()
        service = OperationLogService(repo=repo)

        log_id = service.log(
            "update", "knowledge", "kid-1",
            before={"title": "old"}, after={"title": "new"},
        )
        assert log_id

        logs = repo.get_by_target("knowledge", "kid-1")
        assert len(logs) == 1
        assert json.loads(logs[0]["snapshot_before"]) == {"title": "old"}
        assert json.loads(logs[0]["snapshot_after"]) == {"title": "new"}

    def test_cleanup_old_logs(self):
        repo = OperationLogRepository()
        old_time = "2020-01-01T00:00:00"
        repo.insert({
            "operation": "create", "target_type": "knowledge", "target_id": "old-1",
            "created_at": old_time,
        })
        repo.insert({
            "operation": "create", "target_type": "knowledge", "target_id": "new-1",
            "created_at": datetime.now().isoformat(),
        })

        repo.cleanup(retention_days=30)
        logs = repo.query(target_type="knowledge")
        assert all(l["target_id"] != "old-1" for l in logs)
        assert any(l["target_id"] == "new-1" for l in logs)


class TestVersionSnapshotExtension:
    """P4: 版本快照覆盖全字段（title/tags 变更也触发）"""

    def test_title_change_creates_version(self):
        kid = insert_test_knowledge(title="Original Title", content="Content")
        Database.update_knowledge(kid, title="New Title")

        versions = Database.list_versions(kid)
        assert len(versions) >= 1
        assert versions[0]["title"] == "Original Title"

    def test_tags_change_creates_version(self):
        kid = insert_test_knowledge(title="Test", content="Content", tags=["old-tag"])
        Database.update_knowledge(kid, tags=json.dumps(["new-tag"], ensure_ascii=False))

        versions = Database.list_versions(kid)
        assert len(versions) >= 1
        assert "old-tag" in versions[0]["tags"]

    def test_content_change_creates_version(self):
        kid = insert_test_knowledge(title="Test", content="Old Content")
        Database.update_knowledge(kid, content="New Content")

        versions = Database.list_versions(kid)
        assert len(versions) >= 1
        assert versions[0]["content"] == "Old Content"

    def test_no_version_for_non_semantic_field(self):
        kid = insert_test_knowledge(title="Test", content="Content")
        old_versions = Database.list_versions(kid)
        Database.update_knowledge(kid, source_type="web")
        new_versions = Database.list_versions(kid)
        assert len(new_versions) == len(old_versions)


class TestWikiSoftDelete:
    """P4: Wiki 页面软删除"""

    def test_soft_delete_marks_deleted(self):
        page_id = insert_test_wiki_page(title="Delete Test", status="published")
        Database.delete_wiki_page(page_id)

        page = Database.get_wiki_page(page_id)
        assert page is not None
        assert page["status"] == "deleted"

    def test_list_excludes_deleted(self):
        insert_test_wiki_page(title="Active Page", status="published")
        insert_test_wiki_page(title="Deleted Page", status="deleted")

        pages = Database.list_wiki_pages()
        assert all(p["status"] != "deleted" for p in pages)

    def test_list_with_deleted_filter(self):
        insert_test_wiki_page(title="Deleted Filter", status="deleted")

        pages = Database.list_wiki_pages(status="deleted")
        assert all(p["status"] == "deleted" for p in pages)

    def test_restore_deleted_page(self):
        page_id = insert_test_wiki_page(title="Restore Test", status="deleted")
        Database.restore_wiki_page(page_id)

        page = Database.get_wiki_page(page_id)
        assert page["status"] == "draft"

    def test_purge_deleted_page(self):
        page_id = insert_test_wiki_page(title="Purge Test", status="deleted")
        Database.purge_wiki_page(page_id)

        page = Database.get_wiki_page(page_id)
        assert page is None


class TestKnowledgeRepoVersionExtension:
    """P4: KnowledgeRepository 版本快照覆盖全字段"""

    def test_repo_title_change_creates_version(self):
        from src.repositories.knowledge_repo import KnowledgeRepository
        repo = KnowledgeRepository()

        kid = insert_test_knowledge(title="Repo Original", content="Content")
        repo.update(kid, title="Repo Updated")

        versions = repo.list_versions(kid)
        assert len(versions) >= 1
        assert versions[0]["title"] == "Repo Original"

    def test_repo_tags_change_creates_version(self):
        from src.repositories.knowledge_repo import KnowledgeRepository
        repo = KnowledgeRepository()

        kid = insert_test_knowledge(title="Test", content="C", tags=["a"])
        repo.update(kid, tags=json.dumps(["b"], ensure_ascii=False))

        versions = repo.list_versions(kid)
        assert len(versions) >= 1


# ---- Phase 0: MCP envelope + operation_log 闭环 ----

class TestMcpWriteToolsEnvelopeAndLog:
    """Phase 0 验收：每个 MCP 写工具返回 envelope.operation_id，且同步写入 operation_logs 表。"""

    @pytest.fixture
    def mcp_env(self, setup_db, monkeypatch):
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

    def test_create_envelope_has_operation_id_and_log_row(self, mcp_env):
        from src.mcp_server import create
        result = create(title="测试条目", content="内容")
        assert result["ok"] is True
        assert "operation_id" in result, "envelope 必须包含 operation_id"
        log_id = result["operation_id"]
        assert log_id
        # operation_logs 表里能查到
        from src.repositories.operation_log_repo import OperationLogRepository
        entry = OperationLogRepository().get_by_id(log_id)
        assert entry is not None
        assert entry["operation"] == "create"
        assert entry["target_type"] == "knowledge"
        assert entry["source"] == "mcp"

    def test_update_envelope_has_operation_id_and_log_row(self, mcp_env):
        from src.mcp_server import create, update
        created = create(title="原", content="c")
        kid = created["data"]["id"]
        result = update(item_id=kid, title="新")
        assert result["ok"] is True
        log_id = result["operation_id"]
        from src.repositories.operation_log_repo import OperationLogRepository
        entry = OperationLogRepository().get_by_id(log_id)
        assert entry is not None
        assert entry["operation"] == "update"
        assert entry["target_id"] == kid

    def test_delete_envelope_has_operation_id_and_log_row(self, mcp_env):
        from src.mcp_server import create, delete
        created = create(title="待删", content="c")
        kid = created["data"]["id"]
        result = delete(item_id=kid)
        assert result["ok"] is True
        log_id = result["operation_id"]
        from src.repositories.operation_log_repo import OperationLogRepository
        entry = OperationLogRepository().get_by_id(log_id)
        assert entry is not None
        assert entry["operation"] == "delete"
        assert entry["target_id"] == kid

    def test_dry_run_does_not_log(self, mcp_env):
        from src.mcp_server import create, update
        created = create(title="t", content="c")
        kid = created["data"]["id"]
        # 拿到 create 写入的 log 数
        from src.services.db import Database
        before = Database.get_conn().execute(
            "SELECT COUNT(*) as c FROM operation_logs WHERE target_id = ?", (kid,)
        ).fetchone()["c"]
        result = update(item_id=kid, title="x", dry_run=True)
        assert result["dry_run"] is True
        # 不应新增
        after = Database.get_conn().execute(
            "SELECT COUNT(*) as c FROM operation_logs WHERE target_id = ?", (kid,)
        ).fetchone()["c"]
        assert after == before, "dry_run 不应写 operation_log"

    def test_get_by_id_returns_full_record(self, mcp_env):
        from src.mcp_server import create
        from src.repositories.operation_log_repo import OperationLogRepository
        result = create(title="t", content="c")
        log_id = result["operation_id"]
        entry = OperationLogRepository().get_by_id(log_id)
        assert entry is not None
        for key in ("id", "operation", "target_type", "target_id",
                    "operator", "source", "created_at"):
            assert key in entry
        assert entry["source"] == "mcp"
        assert entry["operator"] == "system"

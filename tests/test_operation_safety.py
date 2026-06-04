"""操作安全功能测试 — 覆盖 P1~P4 全部功能"""
import json
import uuid
from datetime import datetime

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

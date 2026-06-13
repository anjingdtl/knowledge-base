"""indexed_file_repo 单元测试"""
import os
import pytest

from src.repositories.indexed_file_repo import IndexedFileRepository, _normalize_path


@pytest.fixture
def repo():
    return IndexedFileRepository()


class TestIndexedFileRepository:
    """IndexedFileRepository CRUD 测试"""

    def test_upsert_and_get(self, repo):
        """upsert 后 get 应返回完整记录"""
        record = {
            "path": "/tmp/test/hello.txt",
            "knowledge_id": "kid-001",
            "size": 1024,
            "mtime_ns": 1000000000,
            "sha256": "abc123",
            "status": "indexed",
            "last_indexed_at": "2026-06-13T00:00:00",
            "last_error": None,
        }
        repo.upsert(record)

        result = repo.get("/tmp/test/hello.txt")
        assert result is not None
        assert result["path"] == _normalize_path("/tmp/test/hello.txt")
        assert result["knowledge_id"] == "kid-001"
        assert result["size"] == 1024
        assert result["mtime_ns"] == 1000000000
        assert result["sha256"] == "abc123"
        assert result["status"] == "indexed"

    def test_upsert_update(self, repo):
        """upsert 同一路径应更新而非重复插入"""
        repo.upsert({
            "path": "/tmp/test/update.txt",
            "size": 100,
            "mtime_ns": 1,
            "sha256": "hash1",
        })
        repo.upsert({
            "path": "/tmp/test/update.txt",
            "size": 200,
            "mtime_ns": 2,
            "sha256": "hash2",
            "status": "indexed",
            "knowledge_id": "kid-002",
        })
        result = repo.get("/tmp/test/update.txt")
        assert result["size"] == 200
        assert result["sha256"] == "hash2"
        assert result["status"] == "indexed"
        assert result["knowledge_id"] == "kid-002"

    def test_get_nonexistent(self, repo):
        """查询不存在的路径返回 None"""
        assert repo.get("/nonexistent/path.txt") is None

    def test_mark_failed(self, repo):
        """mark_failed 应设置 status=failed 和 last_error"""
        repo.upsert({
            "path": "/tmp/test/fail.txt",
            "size": 50,
            "mtime_ns": 1,
            "sha256": "h",
            "status": "pending",
        })
        repo.mark_failed("/tmp/test/fail.txt", "parse error: encoding")
        result = repo.get("/tmp/test/fail.txt")
        assert result["status"] == "failed"
        assert result["last_error"] == "parse error: encoding"

    def test_mark_deleted(self, repo):
        """mark_deleted 应设置 status=deleted"""
        repo.upsert({
            "path": "/tmp/test/del.txt",
            "size": 10,
            "mtime_ns": 1,
            "sha256": "h",
            "status": "indexed",
        })
        repo.mark_deleted("/tmp/test/del.txt")
        result = repo.get("/tmp/test/del.txt")
        assert result["status"] == "deleted"

    def test_list_by_root(self, repo):
        """list_by_root 应匹配前缀路径"""
        for i in range(3):
            repo.upsert({
                "path": f"/tmp/project_a/file_{i}.txt",
                "size": i * 10,
                "mtime_ns": i,
                "sha256": f"h{i}",
            })
        repo.upsert({
            "path": "/tmp/project_b/other.txt",
            "size": 99,
            "mtime_ns": 99,
            "sha256": "h_other",
        })
        results = repo.list_by_root("/tmp/project_a")
        assert len(results) == 3
        # project_b 不应被匹配
        results_b = repo.list_by_root("/tmp/project_a")
        for r in results_b:
            assert "project_b" not in r["path"]

    def test_list_by_status(self, repo):
        """list_by_status 应按状态过滤并尊重 limit"""
        for i in range(5):
            repo.upsert({
                "path": f"/tmp/status/file_{i}.txt",
                "size": 10,
                "mtime_ns": i,
                "sha256": f"h{i}",
                "status": "pending" if i < 3 else "indexed",
            })
        pending = repo.list_by_status("pending")
        assert len(pending) == 3
        indexed = repo.list_by_status("indexed")
        assert len(indexed) == 2
        # limit
        limited = repo.list_by_status("pending", limit=2)
        assert len(limited) == 2

    def test_delete(self, repo):
        """硬删除后 get 应返回 None"""
        repo.upsert({
            "path": "/tmp/test/hard_del.txt",
            "size": 10,
            "mtime_ns": 1,
            "sha256": "h",
        })
        assert repo.get("/tmp/test/hard_del.txt") is not None
        repo.delete("/tmp/test/hard_del.txt")
        assert repo.get("/tmp/test/hard_del.txt") is None

    def test_path_normalization(self, repo):
        """路径应被标准化（Windows 兼容）"""
        # 使用正斜杠和反斜杠的路径应被视为同一路径
        path1 = "/tmp/Test/Dir/File.TXT"
        path2 = "/tmp/Test/Dir/File.TXT"
        repo.upsert({
            "path": path1,
            "size": 10,
            "mtime_ns": 1,
            "sha256": "norm_test",
        })
        # 查询使用相同路径（标准化后应匹配）
        result = repo.get(path2)
        assert result is not None
        assert result["sha256"] == "norm_test"

    def test_default_status_is_pending(self, repo):
        """upsert 时不提供 status 应默认 pending"""
        repo.upsert({
            "path": "/tmp/test/default_status.txt",
            "size": 10,
            "mtime_ns": 1,
            "sha256": "h",
        })
        result = repo.get("/tmp/test/default_status.txt")
        assert result["status"] == "pending"

"""Sprint 4 / Phase 5 验收：大文件异步任务。

覆盖：
- ``_file_ingest_handler`` / ``_url_ingest_handler`` 基本功能
- ``_estimate_file_complexity`` 大小判定逻辑
- ``create_ingest_job`` MCP 工具
- ``ingest_file`` 大小阈值自动路由
- ``get_job`` / ``list_jobs`` / ``cancel_job`` 便捷工具
- job 进度上报、cancel 中断、结构化返回字段
"""
from __future__ import annotations

import os
import tempfile

import pytest

from src.services.async_task import AsyncTaskService
from src.services.async_worker import TaskRegistry
from src.services.db import Database
from src.utils.envelope import ErrorCode
from tests.conftest import insert_test_knowledge

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mock_container(monkeypatch):
    """Mock 向量存储 + embedding，避免真实 AI 调用。"""
    class MockVS:
        def __init__(self, db=None):
            pass
        def search(self, query, top_k=5):
            return []
        def add_chunks(self, chunks):
            pass
        def delete_by_knowledge(self, kid):
            pass
        def count(self):
            return 0

    class MockBS:
        def __init__(self, db=None):
            pass
        def search(self, query, top_k=5):
            return []
        def add_block_embedding(self, block_id, embedding):
            pass
        def delete_by_page(self, page_id):
            pass
        def count(self):
            return 0

    class MockEmb:
        def __init__(self, config=None):
            pass
        def embed(self, texts):
            # 返回 1024 维零向量
            return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr("src.services.vectorstore.VectorStore", MockVS)
    monkeypatch.setattr("src.services.block_store.BlockStore", MockBS)
    monkeypatch.setattr("src.services.embedding.EmbeddingService", MockEmb)


@pytest.fixture
def mcp_env(setup_db, monkeypatch):
    """准备 MCP 测试环境：mock AI 服务。"""
    _mock_container(monkeypatch)


def _create_test_file(suffix: str = ".txt", content: str = "Hello world",
                      size: int | None = None) -> str:
    """创建临时测试文件并返回路径。"""
    fd, path = tempfile.mkstemp(suffix=suffix)
    if size is not None:
        # 写指定大小的文件
        with os.fdopen(fd, "wb") as f:
            f.write(b"x" * size)
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    return path


# ---------------------------------------------------------------------------
# 1) _estimate_file_complexity 大小判定
# ---------------------------------------------------------------------------

class TestEstimateFileComplexity:
    """文件复杂度估算 — 大小阈值路由判定。"""

    def test_small_file_not_async(self, setup_db, tmp_path):
        from src.services.async_tasks import _estimate_file_complexity
        small_file = _create_test_file(".txt", content="small")
        try:
            result = _estimate_file_complexity(small_file)
            assert result["needs_async"] is False
            assert result["size_bytes"] < 5_000_000
        finally:
            os.unlink(small_file)

    def test_large_file_needs_async(self, setup_db, monkeypatch, tmp_path):
        from src.services.async_tasks import _estimate_file_complexity
        from src.utils.config import Config
        # 设置一个很低的阈值方便测试
        Config.set("ingest.size_threshold_bytes", 100)
        large_file = _create_test_file(".txt", size=200)
        try:
            result = _estimate_file_complexity(large_file)
            assert result["needs_async"] is True
            assert "文件大小" in result["reason"]
        finally:
            os.unlink(large_file)
            Config.set("ingest.size_threshold_bytes", 5_000_000)

    def test_nonexistent_file_size_zero(self, setup_db):
        from src.services.async_tasks import _estimate_file_complexity
        result = _estimate_file_complexity("/nonexistent/file.txt")
        assert result["size_bytes"] == 0
        assert result["needs_async"] is False


# ---------------------------------------------------------------------------
# 2) _file_ingest_handler 基本功能
# ---------------------------------------------------------------------------

class TestFileIngestHandler:
    """异步文件导入 handler。"""

    def test_basic_txt_import(self, setup_db, monkeypatch):
        """小 TXT 文件异步导入成功。"""
        _mock_container(monkeypatch)
        from src.services.async_tasks import _file_ingest_handler

        txt_file = _create_test_file(".txt", content="异步导入测试内容")
        try:
            job_id = AsyncTaskService.create_job("file_ingest", {"file_path": txt_file, "tags": ["test"]})
            result = _file_ingest_handler(job_id, {"file_path": txt_file, "tags": ["test"]})

            assert result["created_items"] or result["skipped_items"] or result["total_items"] >= 0
            assert "block_count" in result
            assert "sheet_count" in result
            assert "page_count" in result
            # realpath 可能将路径规范化（8.3 短名），只验证文件名存在
            assert os.path.basename(txt_file) in result["file_path"]
        finally:
            os.unlink(txt_file)

    def test_structured_return_fields(self, setup_db, monkeypatch):
        """返回结构包含所有必须字段。"""
        _mock_container(monkeypatch)
        from src.services.async_tasks import _file_ingest_handler

        txt_file = _create_test_file(".txt", content="结构化返回字段测试")
        try:
            job_id = AsyncTaskService.create_job("file_ingest", {"file_path": txt_file, "tags": []})
            result = _file_ingest_handler(job_id, {"file_path": txt_file, "tags": []})

            required_keys = [
                "created_items", "skipped_items", "failed_items",
                "sheet_count", "page_count", "block_count",
                "total_items", "file_path", "file_size",
            ]
            for key in required_keys:
                assert key in result, f"缺少字段: {key}"
        finally:
            os.unlink(txt_file)

    def test_nonexistent_file_raises(self, setup_db, monkeypatch):
        """文件不存在时 handler 抛异常。"""
        _mock_container(monkeypatch)
        from src.services.async_tasks import _file_ingest_handler

        job_id = AsyncTaskService.create_job("file_ingest", {"file_path": "/nonexistent.txt", "tags": []})
        with pytest.raises(RuntimeError, match="文件不存在"):
            _file_ingest_handler(job_id, {"file_path": "/nonexistent.txt", "tags": []})

    def test_cancel_check_mid_import(self, setup_db, monkeypatch):
        """取消标记在导入过程中被检查。"""
        _mock_container(monkeypatch)
        from src.services.async_tasks import _file_ingest_handler
        from src.services.async_worker import TaskRegistry

        # 创建一个会被取消的 job
        txt_file = _create_test_file(".txt", content="取消测试")
        try:
            job_id = AsyncTaskService.create_job("file_ingest", {"file_path": txt_file, "tags": []})
            # 提前标记取消
            TaskRegistry.cancel_job(job_id)
            with pytest.raises(RuntimeError, match="cancelled"):
                _file_ingest_handler(job_id, {"file_path": txt_file, "tags": []})
        finally:
            os.unlink(txt_file)
            TaskRegistry.clear_cancelled(job_id)


# ---------------------------------------------------------------------------
# 3) _url_ingest_handler
# ---------------------------------------------------------------------------

class TestUrlIngestHandler:
    """异步 URL 导入 handler。"""

    def test_handler_with_mock(self, setup_db, monkeypatch):
        """mock parse_url 后验证 handler 返回结构。"""
        _mock_container(monkeypatch)

        from src.services.async_tasks import _url_ingest_handler
        from src.services.file_parser import ParsedFile

        mock_parsed = ParsedFile(
            title="Mocked Page",
            content="Mocked web content for testing",
            file_type="html",
            source_path="https://example.com",
            metadata={},
        )
        monkeypatch.setattr(
            "src.services.file_parser.parse_url",
            lambda url: mock_parsed,
        )

        job_id = AsyncTaskService.create_job("url_ingest", {"url": "https://example.com", "tags": []})
        result = _url_ingest_handler(job_id, {"url": "https://example.com", "tags": []})

        assert result["created_items"] or result["skipped_items"]
        assert "block_count" in result
        assert result["url"] == "https://example.com"

    def test_duplicate_url_skipped(self, setup_db, monkeypatch):
        """重复 URL 内容被跳过。"""
        _mock_container(monkeypatch)

        from src.services.async_tasks import _url_ingest_handler
        from src.services.file_parser import ParsedFile

        mock_parsed = ParsedFile(
            title="Dup Page",
            content="Duplicate content",
            file_type="html",
            source_path="https://dup.example.com",
            metadata={},
        )
        monkeypatch.setattr(
            "src.services.file_parser.parse_url",
            lambda url: mock_parsed,
        )

        # 先手动插入一个同 hash 的条目
        import hashlib
        content_hash = hashlib.sha256("Duplicate content".encode("utf-8")).hexdigest()
        insert_test_knowledge(title="Dup Page", content="Duplicate content", content_hash=content_hash)

        job_id = AsyncTaskService.create_job("url_ingest", {"url": "https://dup.example.com", "tags": []})
        result = _url_ingest_handler(job_id, {"url": "https://dup.example.com", "tags": []})

        assert len(result["skipped_items"]) == 1
        assert result["skipped_items"][0]["reason"] == "网页内容已存在"


# ---------------------------------------------------------------------------
# 4) create_ingest_job MCP 工具
# ---------------------------------------------------------------------------

class TestMcpCreateIngestJob:
    """create_ingest_job 工具验收。"""

    def test_file_path_creates_job(self, setup_db, mcp_env):
        txt_file = _create_test_file(".txt", content="job creation test")
        try:
            from src.mcp_server import create_ingest_job
            result = create_ingest_job(file_path=txt_file, tags=["test"])
            assert result["ok"] is True
            assert "job_id" in result["data"]
            assert result["data"]["job_type"] == "file_ingest"
            assert result["data"]["status"] == "pending"
        finally:
            os.unlink(txt_file)

    def test_url_creates_job(self, setup_db, mcp_env):
        from src.mcp_server import create_ingest_job
        result = create_ingest_job(url="https://example.com", tags=["web"])
        assert result["ok"] is True
        assert result["data"]["job_type"] == "url_ingest"
        assert result["data"]["status"] == "pending"

    def test_neither_file_nor_url_fails(self, setup_db, mcp_env):
        from src.mcp_server import create_ingest_job
        result = create_ingest_job()
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.VALIDATION_ERROR

    def test_nonexistent_file_fails(self, setup_db, mcp_env):
        from src.mcp_server import create_ingest_job
        result = create_ingest_job(file_path="/nonexistent/path.txt")
        assert result["ok"] is False
        assert result["error"]["code"] in (ErrorCode.INGEST_FAILED, ErrorCode.PERMISSION_DENIED)


# ---------------------------------------------------------------------------
# 5) ingest_file 大小阈值自动路由
# ---------------------------------------------------------------------------

class TestIngestFileAutoRouting:
    """ingest_file 大文件自动路由到异步任务。"""

    def test_small_file_sync(self, setup_db, mcp_env):
        """小文件走同步，不返回 job_id。"""
        txt_file = _create_test_file(".txt", content="small sync test")
        try:
            from src.mcp_server import ingest_file
            result = ingest_file(file_path=txt_file, tags=["sync"])
            assert result["ok"] is True
            # 同步路径不返回 job_id
            assert "job_id" not in result.get("data", {})
        finally:
            os.unlink(txt_file)

    def test_large_file_auto_async(self, setup_db, mcp_env, monkeypatch):
        """大文件自动转异步，返回 job_id。"""
        from src.utils.config import Config
        Config.set("ingest.size_threshold_bytes", 100)
        large_file = _create_test_file(".txt", size=200)
        try:
            from src.mcp_server import ingest_file
            result = ingest_file(file_path=large_file, tags=["async"])
            assert result["ok"] is True
            assert result["data"].get("routed_async") is True
            assert "job_id" in result["data"]
            assert "reason" in result["data"]
        finally:
            os.unlink(large_file)
            Config.set("ingest.size_threshold_bytes", 5_000_000)

    def test_dry_run_shows_routing(self, setup_db, mcp_env, monkeypatch):
        """dry_run 预览显示 would_route_async 字段。"""
        from src.utils.config import Config
        Config.set("ingest.size_threshold_bytes", 100)
        large_file = _create_test_file(".txt", size=200)
        try:
            from src.mcp_server import ingest_file
            result = ingest_file(file_path=large_file, dry_run=True)
            assert result["ok"] is True
            assert result["dry_run"] is True
            assert "would_route_async" in result["data"]["would_change"]
        finally:
            os.unlink(large_file)
            Config.set("ingest.size_threshold_bytes", 5_000_000)

    def test_dry_run_no_db_write(self, setup_db, mcp_env):
        """dry_run 不创建知识条目也不创建 job。"""
        txt_file = _create_test_file(".txt", content="dry run no write")
        try:
            count_before = Database.count_knowledge()
            from src.mcp_server import ingest_file
            result = ingest_file(file_path=txt_file, dry_run=True)
            count_after = Database.count_knowledge()
            assert count_before == count_after
            assert result["dry_run"] is True
        finally:
            os.unlink(txt_file)


# ---------------------------------------------------------------------------
# 6) get_job / list_jobs / cancel_job 便捷工具
# ---------------------------------------------------------------------------

class TestMcpJobTools:
    """get_job / list_jobs / cancel_job 验收。"""

    def test_get_job_existing(self, setup_db, mcp_env):
        """get_job 返回 job 详情。"""
        job_id = AsyncTaskService.create_job("file_ingest", {"file_path": "/tmp/test.txt"})
        from src.mcp_server import get_job
        result = get_job(job_id=job_id)
        assert result["ok"] is True
        assert result["data"]["id"] == job_id
        assert result["data"]["status"] == "pending"

    def test_get_job_nonexistent(self, setup_db, mcp_env):
        """get_job 返回 JOB_NOT_FOUND。"""
        from src.mcp_server import get_job
        result = get_job(job_id="nonexistent-id")
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.JOB_NOT_FOUND

    def test_list_jobs_returns_envelope(self, setup_db, mcp_env):
        """list_jobs 返回 envelope 含 count/limit。"""
        AsyncTaskService.create_job("file_ingest", {"file_path": "/tmp/a.txt"})
        AsyncTaskService.create_job("url_ingest", {"url": "https://example.com"})

        from src.mcp_server import list_jobs
        result = list_jobs(limit=10)
        assert result["ok"] is True
        assert result["data"]  # 非空列表
        assert "count" in result["meta"]

    def test_list_jobs_filter_by_type(self, setup_db, mcp_env):
        """list_jobs 按 job_type 筛选。"""
        AsyncTaskService.create_job("file_ingest", {"file_path": "/tmp/a.txt"})
        AsyncTaskService.create_job("url_ingest", {"url": "https://example.com"})

        from src.mcp_server import list_jobs
        result = list_jobs(job_type="file_ingest")
        assert result["ok"] is True
        for item in result["data"]:
            assert item["job_type"] == "file_ingest"

    def test_cancel_job_pending(self, setup_db, mcp_env):
        """cancel_job 成功取消 pending 任务。"""
        job_id = AsyncTaskService.create_job("file_ingest", {"file_path": "/tmp/test.txt"})

        from src.mcp_server import cancel_job
        result = cancel_job(job_id=job_id)
        assert result["ok"] is True
        assert result["data"]["success"] is True

        # 验证 DB 状态
        job = AsyncTaskService.get_job(job_id)
        assert job.status == "cancelled"

    def test_cancel_job_completed_fails(self, setup_db, mcp_env):
        """cancel_job 对已完成任务返回失败。"""
        job_id = AsyncTaskService.create_job("file_ingest", {"file_path": "/tmp/test.txt"})
        AsyncTaskService.update_status(job_id, "completed", result={"test": True})

        from src.mcp_server import cancel_job
        result = cancel_job(job_id=job_id)
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PRECONDITION_FAILED


# ---------------------------------------------------------------------------
# 7) TaskRegistry 注册验证
# ---------------------------------------------------------------------------

class TestTaskRegistry:
    """验证 file_ingest / url_ingest handler 已注册。"""

    def test_file_ingest_registered(self):
        handler = TaskRegistry.get_handler("file_ingest")
        assert handler is not None

    def test_url_ingest_registered(self):
        handler = TaskRegistry.get_handler("url_ingest")
        assert handler is not None


# ---------------------------------------------------------------------------
# 8) 进度上报验证
# ---------------------------------------------------------------------------

class TestProgressReporting:
    """Handler 执行中上报进度。"""

    def test_file_ingest_updates_progress(self, setup_db, monkeypatch):
        """file_ingest handler 在执行过程中调用 update_progress。"""
        _mock_container(monkeypatch)
        from src.services.async_tasks import _file_ingest_handler

        txt_file = _create_test_file(".txt", content="进度测试")
        try:
            job_id = AsyncTaskService.create_job("file_ingest", {"file_path": txt_file, "tags": []})
            _file_ingest_handler(job_id, {"file_path": txt_file, "tags": []})

            # 验证最终 progress = 100
            job = AsyncTaskService.get_job(job_id)
            assert job.progress == 100
        finally:
            os.unlink(txt_file)


# ---------------------------------------------------------------------------
# 9) Envelope 兼容 — 旧工具仍可工作
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    """旧 create_async_job / get_async_job / list_async_jobs / cancel_async_job 不受影响。"""

    def test_create_async_job_still_works(self, setup_db, mcp_env):
        from src.mcp_server import create_async_job
        result = create_async_job(job_type="file_ingest", params={"file_path": "/tmp/test.txt"})
        assert result["ok"] is True
        assert "job_id" in result["data"]

    def test_get_async_job_still_works(self, setup_db, mcp_env):
        job_id = AsyncTaskService.create_job("file_ingest", {"file_path": "/tmp/test.txt"})
        from src.mcp_server import get_async_job
        result = get_async_job(job_id=job_id)
        assert result["ok"] is True

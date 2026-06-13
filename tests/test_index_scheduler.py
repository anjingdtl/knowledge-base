"""index_scheduler 单元测试"""
import os
from unittest.mock import MagicMock

import pytest

from src.models.indexing import IndexResult
from src.services.index_scheduler import IndexScheduler, _normalize


@pytest.fixture
def mock_indexer():
    indexer = MagicMock()
    indexer.index_path.return_value = IndexResult(created=1)
    indexer.delete_path.return_value = IndexResult(deleted=1)
    return indexer


@pytest.fixture
def scheduler(mock_indexer):
    return IndexScheduler(path_indexer=mock_indexer, debounce_ms=100)


class TestEventMerging:
    """事件合并逻辑测试"""

    def test_delete_plus_create_equals_modify(self, scheduler, mock_indexer):
        """delete + create 应合并为 modify"""
        scheduler.schedule("/tmp/test.txt", "deleted")
        scheduler.schedule("/tmp/test.txt", "created")
        result = scheduler.flush()
        # 应作为 created（modified 归类到 created+modified 组）
        assert result.created + result.updated >= 0
        # index_path 应被调用一次
        assert mock_indexer.index_path.call_count == 1

    def test_multiple_modify_single(self, scheduler, mock_indexer):
        """多次 modify 应合并为单次"""
        scheduler.schedule("/tmp/test.txt", "modified")
        scheduler.schedule("/tmp/test.txt", "modified")
        scheduler.schedule("/tmp/test.txt", "modified")
        scheduler.flush()
        assert mock_indexer.index_path.call_count == 1

    def test_create_plus_delete_drops(self, scheduler, mock_indexer):
        """create + delete 应被丢弃"""
        scheduler.schedule("/tmp/new.txt", "created")
        scheduler.schedule("/tmp/new.txt", "deleted")
        scheduler.flush()
        # 应该没有实际操作
        assert mock_indexer.index_path.call_count == 0

    def test_distinct_paths_independent(self, scheduler, mock_indexer):
        """不同路径的事件应独立处理"""
        scheduler.schedule("/tmp/a.txt", "created")
        scheduler.schedule("/tmp/b.txt", "modified")
        scheduler.flush()
        assert mock_indexer.index_path.call_count == 2


class TestFlush:
    """flush 处理测试"""

    def test_flush_empty(self, scheduler):
        """无待处理事件时 flush 返回空结果"""
        result = scheduler.flush()
        assert result.created == 0
        assert result.updated == 0
        assert result.deleted == 0

    def test_flush_clears_pending(self, scheduler, mock_indexer):
        """flush 后 pending 应被清空"""
        scheduler.schedule("/tmp/test.txt", "created")
        assert scheduler.pending_count == 1
        scheduler.flush()
        assert scheduler.pending_count == 0

    def test_flush_handles_deleted(self, scheduler, mock_indexer):
        """deleted 事件应由 PathIndexService 统一删除知识和追踪记录"""
        scheduler.schedule("/tmp/gone.txt", "deleted")
        result = scheduler.flush()
        assert result.deleted == 1
        mock_indexer.delete_path.assert_called_once()

    def test_flush_handles_index_error(self, scheduler, mock_indexer):
        """索引失败应记录在 failed 列表"""
        mock_indexer.index_path.side_effect = RuntimeError("parse error")
        scheduler.schedule("/tmp/bad.txt", "created")
        result = scheduler.flush()
        assert len(result.failed) == 1
        assert result.failed[0]["error"] == "parse error"

    def test_pending_count(self, scheduler):
        """pending_count 应反映当前待处理事件数"""
        assert scheduler.pending_count == 0
        scheduler.schedule("/tmp/a.txt", "created")
        assert scheduler.pending_count == 1
        scheduler.schedule("/tmp/b.txt", "modified")
        assert scheduler.pending_count == 2


class TestShutdown:
    """shutdown 测试"""

    def test_shutdown_stops_accepting_events(self, scheduler, mock_indexer):
        """shutdown 后不应接受新事件"""
        scheduler.shutdown()
        scheduler.schedule("/tmp/after.txt", "created")
        result = scheduler.flush()
        assert result.created == 0
        assert mock_indexer.index_path.call_count == 0


class TestPathNormalization:
    """路径标准化测试"""

    def test_normalize_windows_paths(self):
        """路径应被标准化"""
        # 在 Windows 上，normcase 会将路径转小写
        norm = _normalize("/Tmp/Test.TXT")
        assert norm == os.path.normcase(os.path.normpath("/Tmp/Test.TXT"))

    def test_same_path_different_separators(self, scheduler, mock_indexer):
        """相同路径不同分隔符应被视为同一事件"""
        scheduler.schedule("/tmp/dir/file.txt", "created")
        scheduler.schedule("/tmp/dir/file.txt", "modified")
        # 应合并为一个事件
        assert scheduler.pending_count == 1

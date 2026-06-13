"""file_watcher 单元测试（mock watchdog）"""
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from src.services.file_watcher import FileWatcher, _normalize
from src.services.index_scheduler import IndexScheduler


@pytest.fixture
def mock_scheduler():
    return MagicMock(spec=IndexScheduler)


@pytest.fixture
def watcher(mock_scheduler, tmp_path):
    return FileWatcher(scheduler=mock_scheduler, root=tmp_path, recursive=True)


class TestFileWatcherStartStop:
    """启停测试"""

    def test_start_sets_running(self, watcher):
        """start 后 is_running 应为 True"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.start.return_value = None
        mock_observer_instance.schedule.return_value = None

        MockObserver = MagicMock(return_value=mock_observer_instance)

        mock_events = MagicMock()
        mock_events.FileSystemEventHandler = type("FakeHandler", (), {})

        with patch.dict("sys.modules", {
            "watchdog": MagicMock(),
            "watchdog.observers": MagicMock(Observer=MockObserver),
            "watchdog.events": mock_events,
        }):
            watcher.start()
        assert watcher.is_running is True

    def test_stop_clears_running(self, watcher):
        """stop 后 is_running 应为 False"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.start.return_value = None
        mock_observer_instance.stop.return_value = None
        mock_observer_instance.join.return_value = None
        mock_observer_instance.schedule.return_value = None

        MockObserver = MagicMock(return_value=mock_observer_instance)

        mock_events = MagicMock()
        mock_events.FileSystemEventHandler = type("FakeHandler", (), {})

        with patch.dict("sys.modules", {
            "watchdog": MagicMock(),
            "watchdog.observers": MagicMock(Observer=MockObserver),
            "watchdog.events": mock_events,
        }):
            watcher.start()
            assert watcher.is_running is True
            watcher.stop()
            assert watcher.is_running is False

    def test_start_without_watchdog_raises(self, watcher, tmp_path):
        """watchdog 未安装时 start 应抛出 RuntimeError"""
        import sys
        # Temporarily hide watchdog
        saved = sys.modules.pop("watchdog", None)
        saved2 = sys.modules.pop("watchdog.observers", None)
        saved3 = sys.modules.pop("watchdog.events", None)
        try:
            with patch.dict("sys.modules", {"watchdog": None, "watchdog.observers": None, "watchdog.events": None}):
                with pytest.raises(RuntimeError, match="watchdog not installed"):
                    watcher.start()
        finally:
            if saved:
                sys.modules["watchdog"] = saved
            if saved2:
                sys.modules["watchdog.observers"] = saved2
            if saved3:
                sys.modules["watchdog.events"] = saved3


class TestEventNormalization:
    """事件标准化测试"""

    def test_normalize_paths(self):
        """_normalize 应标准化路径"""
        result = _normalize("/tmp/Test/File.TXT")
        assert result == os.path.normcase(os.path.normpath("/tmp/Test/File.TXT"))

    def test_watcher_initially_not_running(self, watcher):
        """初始状态 is_running 应为 False"""
        assert watcher.is_running is False

    def test_watcher_root_normalized(self, mock_scheduler, tmp_path):
        """root 路径应被标准化"""
        w = FileWatcher(scheduler=mock_scheduler, root=tmp_path)
        assert str(w._root) == os.path.normcase(os.path.normpath(str(tmp_path)))

    def test_watcher_recursive_flag(self, mock_scheduler, tmp_path):
        """recursive 参数应被保留"""
        w1 = FileWatcher(scheduler=mock_scheduler, root=tmp_path, recursive=True)
        assert w1._recursive is True
        w2 = FileWatcher(scheduler=mock_scheduler, root=tmp_path, recursive=False)
        assert w2._recursive is False

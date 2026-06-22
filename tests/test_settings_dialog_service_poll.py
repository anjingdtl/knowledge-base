"""SettingsDialog 服务操作异步轮询的反馈测试。

ShellExecuteW runas 触发 UAC 后不等待操作完成,旧实现立即刷新状态会让用户
误以为操作没生效。Task 2 引入 QTimer 轮询,应在:
- 启动超时未达 running → 调 _prompt_svc_failure 引导排查(而非默默显示旧状态)
- 启动成功变 running → 中途停止轮询,不打扰用户
- install/remove/configure(expect_running=None) → 仅刷新不判定成败
"""
import os
import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 未安装")
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def settings_dialog(qapp, tmp_path, monkeypatch):
    from src.utils.config import Config

    isolated = Config.__new__(Config)
    isolated._data = {
        "mcp": {"tool_profile": "extended"},
        "llm": {"provider": "openai", "base_url": "https://api.openai.com/v1"},
        "appearance": {"theme": "dark", "font_size": 14},
        "graph_backend": {"provider": "sqlite"},
        "wiki": {"enabled": False},
    }
    monkeypatch.setattr(Config, "_default_instance", isolated)

    from src.gui.settings_dialog import SettingsDialog
    dialog = SettingsDialog()
    yield dialog
    dialog.deleteLater()


class TestServicePollFeedback:
    def test_start_timeout_prompts_troubleshoot(self, settings_dialog, monkeypatch):
        """启动后服务始终 stopped → 8 tick 后触发 _prompt_svc_failure(True)。"""
        from src.services import mcp_launcher
        monkeypatch.setattr(mcp_launcher, "is_service_installed", lambda: True)
        monkeypatch.setattr(mcp_launcher, "get_service_status", lambda: "stopped")
        monkeypatch.setattr(settings_dialog, "_refresh_svc_status", lambda: None)

        prompted = []
        monkeypatch.setattr(
            settings_dialog,
            "_prompt_svc_failure",
            lambda expect: prompted.append(expect),
        )

        settings_dialog._poll_svc_after_uac(expect_running=True)
        for _ in range(8):
            settings_dialog._on_svc_poll_tick()

        assert prompted == [True], "启动超时应调用 _prompt_svc_failure(True)"
        assert not settings_dialog._svc_poll.isActive(), "超时后应停止轮询"

    def test_start_success_no_prompt(self, settings_dialog, monkeypatch):
        """服务变 running → 首次 tick 即 reached,不弹失败提示。"""
        from src.services import mcp_launcher
        monkeypatch.setattr(mcp_launcher, "is_service_installed", lambda: True)
        monkeypatch.setattr(mcp_launcher, "get_service_status", lambda: "running")
        monkeypatch.setattr(settings_dialog, "_refresh_svc_status", lambda: None)

        prompted = []
        monkeypatch.setattr(
            settings_dialog,
            "_prompt_svc_failure",
            lambda expect: prompted.append(expect),
        )

        settings_dialog._poll_svc_after_uac(expect_running=True)
        settings_dialog._on_svc_poll_tick()

        assert prompted == [], "启动成功不应弹失败提示"
        assert not settings_dialog._svc_poll.isActive(), "达成预期后应停止轮询"

    def test_refresh_only_mode_never_prompts(self, settings_dialog, monkeypatch):
        """install/remove/configure(expect_running=None)仅刷新,不判定成败。"""
        from src.services import mcp_launcher
        monkeypatch.setattr(mcp_launcher, "is_service_installed", lambda: True)
        monkeypatch.setattr(mcp_launcher, "get_service_status", lambda: "stopped")
        monkeypatch.setattr(settings_dialog, "_refresh_svc_status", lambda: None)

        prompted = []
        monkeypatch.setattr(
            settings_dialog,
            "_prompt_svc_failure",
            lambda expect: prompted.append(expect),
        )

        settings_dialog._poll_svc_after_uac(expect_running=None)
        for _ in range(8):
            settings_dialog._on_svc_poll_tick()

        assert prompted == [], "仅刷新模式不应弹失败提示"

import os
import socket
import subprocess
import time
from types import SimpleNamespace

import pytest

from src.services import mcp_heartbeat, mcp_launcher


def test_migration_requirement_returns_actionable_message(monkeypatch, tmp_path):
    """数据库启动门禁阻塞时，GUI 应能展示清晰的迁移原因。"""
    db_path = tmp_path / "kb.db"
    plan = SimpleNamespace(
        action="block",
        migration_status=SimpleNamespace(message="数据库版本 j003 落后于 j004"),
    )
    monkeypatch.setattr(mcp_launcher.Config, "get_db_path", lambda: db_path)
    monkeypatch.setattr(
        mcp_launcher,
        "inspect_database_bootstrap",
        lambda *args, **kwargs: plan,
        raising=False,
    )

    requirement = mcp_launcher.get_migration_requirement()

    assert "数据库迁移" in requirement
    assert "j003" in requirement


def test_migrate_database_for_mcp_delegates_to_safe_workflow(monkeypatch, tmp_path):
    """GUI 迁移必须复用会备份、升级和校验的既有工作流。"""
    db_path = tmp_path / "kb.db"
    calls = []
    monkeypatch.setattr(mcp_launcher.Config, "get_db_path", lambda: db_path)
    monkeypatch.setattr(
        mcp_launcher,
        "migrate_database",
        lambda path: calls.append(path) or {"ok": True, "backup": "backup.sqlite"},
        raising=False,
    )

    result = mcp_launcher.migrate_database_for_mcp()

    assert calls == [db_path]
    assert "备份" in result
    assert "backup.sqlite" in result


def test_migrate_database_for_mcp_requires_a_concrete_backup_path(monkeypatch, tmp_path):
    """迁移结果没有备份位置时，不能向 GUI 报告安全迁移成功。"""
    monkeypatch.setattr(mcp_launcher.Config, "get_db_path", lambda: tmp_path / "kb.db")
    monkeypatch.setattr(
        mcp_launcher,
        "migrate_database",
        lambda path: {"ok": True},
    )

    with pytest.raises(RuntimeError, match="备份"):
        mcp_launcher.migrate_database_for_mcp()


def test_mcp_startup_worker_migrates_then_starts_and_emits_one_result(monkeypatch):
    """确认迁移后，耗时的迁移和启动由后台 worker 串行完成。"""
    from src.gui.main_window import MCPStartupWorker

    calls = []
    monkeypatch.setattr(
        mcp_launcher,
        "migrate_database_for_mcp",
        lambda: calls.append("migrate") or "数据库已安全迁移，备份：backup.sqlite",
    )
    monkeypatch.setattr(
        mcp_launcher,
        "start",
        lambda: calls.append("start") or "MCP Server 已启动 (端口 9000)",
    )
    monkeypatch.setattr(mcp_heartbeat, "is_mcp_port_available", lambda: True)
    worker = MCPStartupWorker(migration_required=True)
    results = []
    worker.completed.connect(lambda message, ok: results.append((message, ok)))

    worker.run()

    assert calls == ["migrate", "start"]
    assert results == [
        ("数据库已安全迁移，备份：backup.sqlite\nMCP Server 已启动 (端口 9000)", True)
    ]


def test_mcp_startup_worker_reports_elevation_failure_and_restores_ui(monkeypatch):
    """服务模式的 UAC 提权失败必须被当作启动失败，而非保留开启状态。"""
    from src.gui.main_window import MCPStartupWorker

    monkeypatch.setattr(
        mcp_launcher,
        "start",
        lambda: "提权失败（返回值 5），请手动以管理员身份运行",
    )
    worker = MCPStartupWorker(migration_required=False)
    results = []
    worker.completed.connect(lambda message, ok: results.append((message, ok)))

    worker.run()

    assert results == [("提权失败（返回值 5），请手动以管理员身份运行", False)]


def test_mcp_startup_worker_keeps_accepted_uac_request_pending(monkeypatch):
    """接受 UAC 请求不等于服务已可用，必须保持待确认/离线状态。"""
    from src.gui.main_window import MCPStartupWorker

    monkeypatch.setattr(
        mcp_launcher,
        "start",
        lambda: "已请求启动服务，请确认 UAC 弹窗",
    )
    monkeypatch.setattr(mcp_heartbeat, "is_mcp_port_available", lambda: False)
    worker = MCPStartupWorker(
        migration_required=False,
        readiness_timeout=0,
        readiness_poll_interval=0,
    )
    results = []
    worker.completed.connect(lambda message, ok: results.append((message, ok)))

    worker.run()

    assert results == [
        ("已请求启动服务，请确认 UAC 弹窗\nMCP 服务尚未确认可用，已恢复离线状态。", False)
    ]


def test_mcp_startup_worker_confirms_uac_request_only_after_availability(monkeypatch):
    """UAC 请求本身是 pending；仅在端口真正可达时才升级为成功。"""
    from src.gui.main_window import MCPStartupWorker

    monkeypatch.setattr(
        mcp_launcher,
        "start",
        lambda: "已请求启动服务，请确认 UAC 弹窗",
    )
    monkeypatch.setattr(mcp_heartbeat, "is_mcp_port_available", lambda: True)
    worker = MCPStartupWorker(
        migration_required=False,
        readiness_timeout=0,
        readiness_poll_interval=0,
    )
    results = []
    worker.completed.connect(lambda message, ok: results.append((message, ok)))

    worker.run()

    assert results == [("已请求启动服务，请确认 UAC 弹窗", True)]


def test_mcp_startup_worker_polls_until_mcp_becomes_available(monkeypatch):
    """启动后应在限定时间内轮询，覆盖服务稍晚就绪的正常路径。"""
    from src.gui.main_window import MCPStartupWorker

    availability = iter([False, True])
    monkeypatch.setattr(mcp_launcher, "start", lambda: "MCP Server 已启动 (端口 9000)")
    monkeypatch.setattr(mcp_heartbeat, "is_mcp_port_available", lambda: next(availability))
    worker = MCPStartupWorker(
        migration_required=False,
        readiness_timeout=1,
        readiness_poll_interval=0,
    )
    results = []
    worker.completed.connect(lambda message, ok: results.append((message, ok)))

    worker.run()

    assert results == [("MCP Server 已启动 (端口 9000)", True)]


def test_mcp_startup_worker_times_out_when_mcp_never_becomes_available(monkeypatch):
    """可用性持续离线时必须在超时后恢复离线，而不是无限等待。"""
    from src.gui.main_window import MCPStartupWorker

    monkeypatch.setattr(mcp_launcher, "start", lambda: "MCP Server 已启动 (端口 9000)")
    monkeypatch.setattr(mcp_heartbeat, "is_mcp_port_available", lambda: False)
    worker = MCPStartupWorker(
        migration_required=False,
        readiness_timeout=0,
        readiness_poll_interval=0,
    )
    results = []
    worker.completed.connect(lambda message, ok: results.append((message, ok)))

    worker.run()

    assert results == [
        ("MCP Server 已启动 (端口 9000)\nMCP 服务尚未确认可用，已恢复离线状态。", False)
    ]


def test_mcp_startup_worker_does_not_confirm_from_fresh_heartbeat_alone(monkeypatch):
    """新鲜心跳但 TCP 端口不可达时，启动确认必须保持离线。"""
    from src.gui.main_window import MCPStartupWorker

    monkeypatch.setattr(mcp_launcher, "start", lambda: "MCP Server 已启动 (端口 9000)")
    monkeypatch.setattr(mcp_heartbeat, "is_mcp_available", lambda: True)
    monkeypatch.setattr(
        mcp_heartbeat,
        "is_mcp_port_available",
        lambda: False,
        raising=False,
    )
    worker = MCPStartupWorker(
        migration_required=False,
        readiness_timeout=0,
        readiness_poll_interval=0,
    )
    results = []
    worker.completed.connect(lambda message, ok: results.append((message, ok)))

    worker.run()

    assert results == [
        ("MCP Server 已启动 (端口 9000)\nMCP 服务尚未确认可用，已恢复离线状态。", False)
    ]


class _FakeStatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, *args):
        self.messages.append(args)


class _FakeMcpToggleWindow:
    def __init__(self):
        self.checked_states = []
        self.worker_requests = []
        self._status_bar = _FakeStatusBar()

    def _set_mcp_toggle_checked(self, checked):
        self.checked_states.append(checked)

    def _start_mcp_worker(self, *, migration_required):
        self.worker_requests.append(migration_required)

    def statusBar(self):
        return self._status_bar


def test_mcp_toggle_declining_migration_restores_unchecked_state(monkeypatch):
    """用户拒绝迁移时不启动 worker，并将点击后的开关复位。"""
    from src.gui import main_window

    window = _FakeMcpToggleWindow()
    monkeypatch.setattr(
        mcp_launcher,
        "get_migration_requirement",
        lambda: "MCP 启动前需要数据库迁移：版本落后",
    )
    monkeypatch.setattr(
        main_window.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window.QMessageBox.StandardButton.No,
    )

    main_window.MainWindow._toggle_mcp(window, True)

    assert window.checked_states == [False]
    assert window.worker_requests == []
    assert window._status_bar.messages == [("已取消 MCP 启动", 5000)]


def test_mcp_toggle_accepting_migration_starts_worker_with_migration(monkeypatch):
    """用户确认后，worker 必须收到 migration_required=True。"""
    from src.gui import main_window

    window = _FakeMcpToggleWindow()
    monkeypatch.setattr(
        mcp_launcher,
        "get_migration_requirement",
        lambda: "MCP 启动前需要数据库迁移：版本落后",
    )
    monkeypatch.setattr(
        main_window.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window.QMessageBox.StandardButton.Yes,
    )

    main_window.MainWindow._toggle_mcp(window, True)

    assert window.worker_requests == [True]


def test_mcp_toggle_skips_confirmation_when_no_migration_is_required(monkeypatch):
    """数据库已就绪时直接后台启动，不应弹出迁移确认框。"""
    from src.gui import main_window

    window = _FakeMcpToggleWindow()
    monkeypatch.setattr(mcp_launcher, "get_migration_requirement", lambda: None)
    monkeypatch.setattr(
        main_window.QMessageBox,
        "question",
        lambda *args, **kwargs: pytest.fail("无迁移需求时不应要求确认"),
    )

    main_window.MainWindow._toggle_mcp(window, True)

    assert window.checked_states == []
    assert window.worker_requests == [False]


class _FakeMcpButton:
    def __init__(self):
        self.enabled = []
        self.blocked = []
        self.checked = []
        self.text = []

    def setEnabled(self, value):
        self.enabled.append(value)

    def blockSignals(self, value):
        self.blocked.append(value)

    def setChecked(self, value):
        self.checked.append(value)

    def setText(self, value):
        self.text.append(value)


class _FakeMcpLightStyle:
    def __init__(self):
        self.polished = []

    def polish(self, light):
        self.polished.append(light)


class _FakeMcpLight:
    def __init__(self):
        self.properties = []
        self.text = []
        self._style = _FakeMcpLightStyle()

    def setProperty(self, name, value):
        self.properties.append((name, value))

    def setText(self, value):
        self.text.append(value)

    def style(self):
        return self._style


class _FakeMcpCompletionWindow:
    def __init__(self):
        self.btn_mcp_toggle = _FakeMcpButton()
        self.mcp_light = _FakeMcpLight()
        self._status_bar = _FakeStatusBar()
        self.forced_checks = []
        self.enabled = []
        self._mcp_start_worker = object()

    def _set_mcp_toggle_checked(self, checked):
        from src.gui.main_window import MainWindow

        MainWindow._set_mcp_toggle_checked(self, checked)

    def _check_mcp_status(self, *, force=False):
        self.forced_checks.append(force)
        return False

    def setEnabled(self, value):
        self.enabled.append(value)

    def statusBar(self):
        return self._status_bar


def test_mcp_startup_failure_forces_offline_ui_recovery(monkeypatch):
    """失败回调强制同步后，离线状态必须复位开关、状态灯与操作按钮。"""
    from src.gui import main_window

    window = _FakeMcpCompletionWindow()
    monkeypatch.setattr(main_window, "set_named_icon", lambda *args: None)

    main_window.MainWindow._on_mcp_startup_completed(window, "提权失败", False)

    assert window.forced_checks == []
    assert window.btn_mcp_toggle.enabled == [True]
    assert window.btn_mcp_toggle.checked == [False]
    assert window.mcp_light.properties == [("status", "offline")]
    assert window.mcp_light.text == ["MCP 离线"]
    assert window._status_bar.messages == [("提权失败", 8000)]
    assert window.enabled == [True]
    assert window._mcp_start_worker is not None


class _FakeStaleHeartbeatCompletionWindow(_FakeMcpCompletionWindow):
    def _check_mcp_status(self, *, force=False):
        self.forced_checks.append(force)
        return True


def test_mcp_startup_callback_uses_direct_failure_over_stale_heartbeat(monkeypatch):
    """worker 的 TCP 失败结果不能被新鲜但陈旧的心跳重新标记为在线。"""
    from src.gui import main_window

    window = _FakeStaleHeartbeatCompletionWindow()
    monkeypatch.setattr(main_window, "set_named_icon", lambda *args: None)

    main_window.MainWindow._on_mcp_startup_completed(
        window,
        "MCP 服务尚未确认可用，已恢复离线状态。",
        False,
    )

    assert window.forced_checks == []
    assert window.btn_mcp_toggle.checked == [False]
    assert window.mcp_light.properties == [("status", "offline")]


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _FakeStartupWorker:
    def __init__(self, migration_required):
        self.migration_required = migration_required
        self.completed = _FakeSignal()
        self.finished = _FakeSignal()
        self.started = False

    def start(self):
        self.started = True

    def deleteLater(self):
        pass


class _FakeMcpStartupWindow:
    def __init__(self):
        self.btn_mcp_toggle = _FakeMcpButton()
        self._status_bar = _FakeStatusBar()
        self.enabled = []

    def _on_mcp_startup_completed(self, *args):
        pass

    def _clear_mcp_start_worker(self):
        self._mcp_start_worker = None

    def setEnabled(self, value):
        self.enabled.append(value)

    def statusBar(self):
        return self._status_bar


def test_mcp_startup_disables_main_window_until_worker_completes(monkeypatch):
    """迁移/启动期间禁用主窗口，避免并发 GUI 写入同一数据库。"""
    from src.gui import main_window

    window = _FakeMcpStartupWindow()
    monkeypatch.setattr(main_window, "MCPStartupWorker", _FakeStartupWorker)

    main_window.MainWindow._start_mcp_worker(window, migration_required=True)

    assert window.enabled == [False]
    assert window.btn_mcp_toggle.enabled == [False]
    assert window._mcp_start_worker.migration_required is True
    assert window._mcp_start_worker.started is True


class _FakeCloseWorker:
    def isRunning(self):
        return True


class _FakeCloseEvent:
    def __init__(self):
        self.ignored = False

    def ignore(self):
        self.ignored = True


class _FakeCloseWindow:
    def __init__(self):
        self._mcp_start_worker = _FakeCloseWorker()


def test_close_event_rejects_while_mcp_startup_worker_is_running(monkeypatch):
    """运行中的 QThread 不应在窗口销毁时被销毁。"""
    from src.gui import main_window

    notices = []
    event = _FakeCloseEvent()
    monkeypatch.setattr(
        main_window.QMessageBox,
        "information",
        lambda *args, **kwargs: notices.append(args),
    )

    main_window.MainWindow.closeEvent(_FakeCloseWindow(), event)

    assert event.ignored is True
    assert notices and notices[0][1] == "MCP 正在启动"


def test_status_probe_does_not_spawn_console_process(monkeypatch, tmp_path):
    heartbeat = tmp_path / ".mcp_heartbeat"
    heartbeat.write_text(str(time.time() - 600), encoding="utf-8")
    monkeypatch.setattr(mcp_heartbeat, "_path", heartbeat)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("status probe spawned a subprocess"),
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError()),
    )

    assert mcp_heartbeat.is_mcp_available() is False


def test_launcher_status_does_not_query_windows_service(monkeypatch):
    monkeypatch.setattr(mcp_launcher, "_process", None)
    monkeypatch.setattr(mcp_launcher, "_read_pid", lambda: 12345)
    monkeypatch.setattr(mcp_launcher, "_remove_pid", lambda: None)
    monkeypatch.setattr(
        mcp_launcher,
        "is_service_installed",
        lambda: pytest.fail("periodic status queried sc.exe"),
    )
    monkeypatch.setattr(
        mcp_heartbeat,
        "is_mcp_available",
        lambda: False,
    )

    assert mcp_launcher.is_running() is False


def test_service_registration_requires_pywin32_python_class(monkeypatch):
    """A leftover SCM entry alone must not hijack the GUI start button."""
    monkeypatch.setattr(mcp_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(mcp_launcher, "is_service_installed", lambda: True)

    class Result:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(mcp_launcher, "_run_hidden", lambda *args, **kwargs: Result())

    assert mcp_launcher.is_service_registration_valid() is False


def test_start_falls_back_to_child_process_for_incomplete_service_registration(
    monkeypatch, tmp_path
):
    """GUI MCP start remains usable when an old service lacks PythonClass."""
    launched = {}

    class Process:
        pid = 24680

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        launched["command"] = command
        return Process()

    monkeypatch.setattr(mcp_launcher, "_process", None)
    monkeypatch.setattr(mcp_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(mcp_launcher, "is_running", lambda: bool(mcp_launcher._process))
    monkeypatch.setattr(mcp_launcher, "is_service_installed", lambda: True)
    monkeypatch.setattr(mcp_launcher, "is_service_registration_valid", lambda: False)
    monkeypatch.setattr(
        mcp_launcher,
        "service_start",
        lambda: pytest.fail("incomplete service must not receive the start request"),
    )
    monkeypatch.setattr(mcp_launcher, "_resolve_mcp_python", lambda: tmp_path / "python.exe")
    monkeypatch.setattr(mcp_launcher, "_MCP_SCRIPT", tmp_path / "run_mcp.py")
    monkeypatch.setattr(mcp_launcher, "_STARTUP_LOG_FILE", tmp_path / "mcp-startup.log")
    monkeypatch.setattr(mcp_launcher, "_write_pid", lambda pid: None)
    monkeypatch.setattr(mcp_launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(mcp_launcher.time, "sleep", lambda seconds: None)

    result = mcp_launcher.start(host="127.0.0.1", port=9000)

    assert result.startswith("MCP Server 已启动")
    assert launched["command"][-2:] == ["-p", "9000"]
    mcp_launcher._close_process_log()
    mcp_launcher._process = None


@pytest.mark.skipif(os.name != "nt", reason="Windows creation flags only")
def test_service_queries_hide_console_window(monkeypatch):
    captured = {}

    class Result:
        returncode = 1
        stdout = ""
        stderr = ""

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert mcp_launcher.is_service_installed() is False
    assert captured["creationflags"] & subprocess.CREATE_NO_WINDOW


def test_stop_ignores_pid_that_is_not_an_mcp_process(monkeypatch):
    monkeypatch.setattr(mcp_launcher, "_process", None)
    monkeypatch.setattr(mcp_launcher, "is_service_installed", lambda: False)
    monkeypatch.setattr(mcp_launcher, "_read_pid", lambda: 12345)
    monkeypatch.setattr(mcp_launcher, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(mcp_launcher, "_pid_matches_mcp", lambda pid: False)
    monkeypatch.setattr(mcp_launcher, "_remove_pid", lambda: None)
    monkeypatch.setattr(
        mcp_launcher,
        "_kill_process_windows",
        lambda pid: pytest.fail("stale PID killed an unrelated process"),
    )

    assert mcp_launcher.stop() == "MCP Server 未在运行"


def test_pid_matches_mcp_falls_back_to_cim_when_wmic_missing(monkeypatch):
    """wmic 不可用时应回退 PowerShell CIM 识别 MCP 进程。

    Windows 11 24H2+ 默认不再安装 wmic,旧实现会让 _pid_matches_mcp 恒返回 False,
    导致 stop() 显示「未在运行」却停不掉真实进程。
    """
    monkeypatch.setattr(mcp_launcher, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(mcp_launcher, "_is_windows", lambda: True)

    def wmic_missing(pid):
        raise FileNotFoundError("wmic not found")

    monkeypatch.setattr(mcp_launcher, "_wmic_commandline", wmic_missing)

    cim_calls = {"n": 0}

    def cim_ok(pid):
        cim_calls["n"] += 1
        return r"C:\python\pythonw.exe C:\proj\run_mcp.py -t streamable-http"

    monkeypatch.setattr(mcp_launcher, "_cim_commandline", cim_ok)

    assert mcp_launcher._pid_matches_mcp(12345) is True
    assert cim_calls["n"] == 1, "wmic 失败后应回退 CIM 查询"


def test_pid_matches_mcp_returns_false_when_both_probes_unavailable(monkeypatch):
    """wmic 与 CIM 均不可用时不崩溃,返回 False(交给上层 is_pid_alive 兜底)。"""
    monkeypatch.setattr(mcp_launcher, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(mcp_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(
        mcp_launcher,
        "_wmic_commandline",
        lambda pid: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(mcp_launcher, "_cim_commandline", lambda pid: "")

    assert mcp_launcher._pid_matches_mcp(12345) is False


def test_pid_matches_mcp_prefers_wmic_when_available(monkeypatch):
    """wmic 可用时直接命中,不应再调用较慢的 CIM。"""
    monkeypatch.setattr(mcp_launcher, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(mcp_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(
        mcp_launcher,
        "_wmic_commandline",
        lambda pid: r"C:\python\pythonw.exe run_mcp.py --port 9000",
    )
    monkeypatch.setattr(
        mcp_launcher,
        "_cim_commandline",
        lambda pid: pytest.fail("wmic 可用时不应回退 CIM"),
    )

    assert mcp_launcher._pid_matches_mcp(12345) is True


# ---------- 端口冲突预检(Bug 2 真正根因:服务与子进程模式共用 9000) ----------


class _FakeNetstat:
    def __init__(self, stdout=""):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def test_port_in_use_detects_listener(monkeypatch):
    """netstat 输出含 :9000 LISTENING → 返回占用 PID。"""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: _FakeNetstat(
            "  TCP    127.0.0.1:9000     0.0.0.0:0     LISTENING    29796\r\n"
            "  TCP    0.0.0.0:135        0.0.0.0:0     LISTENING    4\r\n"
        ),
    )
    assert mcp_launcher._port_in_use(9000) == 29796
    assert mcp_launcher._port_in_use(8000) is None


def test_port_in_use_returns_none_when_netstat_fails(monkeypatch):
    """netstat 不可用时不崩溃,返回 None(交由上层放行)。"""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    assert mcp_launcher._port_in_use(9000) is None


def test_service_start_blocks_when_port_occupied(monkeypatch):
    """端口被占 → service_start 返回冲突提示,且不触发 UAC 提权。

    Bug 2 根因:子进程模式 MCP 占着 9000 时,服务启动 uvicorn bind 失败而退出,
    表现为「点启动后状态仍是已停止」。预检把这条路径前移为明确提示。
    """
    monkeypatch.setattr(mcp_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(mcp_launcher, "get_service_status", lambda: "stopped")
    # 迁移预检放行:让测试专注端口冲突分支,不触发真实的 inspect_database_bootstrap
    monkeypatch.setattr(mcp_launcher, "get_migration_requirement", lambda: None)
    monkeypatch.setattr(mcp_launcher, "_port_in_use", lambda port: 12345)
    escalated = []
    monkeypatch.setattr(
        mcp_launcher,
        "_shell_execute_elevated",
        lambda *a, **kw: escalated.append(a) or 99,
    )
    msg = mcp_launcher.service_start()
    assert "端口 9000" in msg and "12345" in msg
    assert escalated == [], "端口被占时不应触发 UAC 提权"


def test_service_start_proceeds_when_port_free(monkeypatch):
    """端口空闲 → 正常走 UAC 提权 sc start。"""
    monkeypatch.setattr(mcp_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(mcp_launcher, "get_service_status", lambda: "stopped")
    monkeypatch.setattr(mcp_launcher, "get_migration_requirement", lambda: None)
    monkeypatch.setattr(mcp_launcher, "_port_in_use", lambda port: None)
    called = {}

    def fake_elevated(exe, params):
        called["args"] = (exe, params)
        return 33  # >32 表示 ShellExecuteW 成功触发 UAC

    monkeypatch.setattr(mcp_launcher, "_shell_execute_elevated", fake_elevated)
    msg = mcp_launcher.service_start()
    assert "UAC" in msg
    assert called["args"][0] == "sc.exe"


def test_service_start_blocks_when_migration_needed(monkeypatch):
    """DB 需要迁移时,service_start 不触发 UAC,直接返回迁移提示。

    服务启动后会触发 lifespan → startup_gate,若 schema 落后于 head 会抛
    MigrationGateError 让 uvicorn 立即崩溃,SCM 把状态留在 STOPPED。
    旧版没有迁移预检,即用户看到的「点启动后状态一直是已停止」表象。
    """
    monkeypatch.setattr(mcp_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(mcp_launcher, "get_service_status", lambda: "stopped")
    monkeypatch.setattr(
        mcp_launcher,
        "get_migration_requirement",
        lambda: "MCP 启动前需要数据库迁移：版本 j003 落后于 j004",
    )
    escalated = []
    monkeypatch.setattr(
        mcp_launcher,
        "_shell_execute_elevated",
        lambda *a, **kw: escalated.append(a) or 99,
    )
    msg = mcp_launcher.service_start()
    assert "数据库迁移" in msg
    assert escalated == [], "迁移阻塞时不应触发 UAC"
    assert "UAC" not in msg, "迁移阻塞消息不应含 UAC (避免误进 GUI 轮询分支)"


def test_service_start_reports_uac_cancellation_instead_of_pending(monkeypatch):
    """用户取消 UAC 时,ShellExecuteW 返回 122 (ERROR_CANCELLED)。

    旧版仅判断 ret<=32,把 122 当作成功,GUI 提示「已请求启动服务」并开始
    8 秒轮询,最后才弹超时排查 —— 即用户看到的「服务状态一直是已停止」。
    修复后必须把 122 当作失败,返回不含 "UAC" 的失败提示让 GUI 走单次刷新分支。
    """
    monkeypatch.setattr(mcp_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(mcp_launcher, "get_service_status", lambda: "stopped")
    monkeypatch.setattr(mcp_launcher, "get_migration_requirement", lambda: None)
    monkeypatch.setattr(mcp_launcher, "_port_in_use", lambda port: None)
    monkeypatch.setattr(mcp_launcher, "_shell_execute_elevated", lambda *a, **kw: 122)
    msg = mcp_launcher.service_start()
    assert "UAC" not in msg, "取消消息不应含 UAC,否则 GUI 会误启动轮询"
    assert "取消" in msg


def test_service_configure_failure_reports_uac_cancellation(monkeypatch):
    """崩溃重启策略:用户取消 UAC 时返回失败提示,不再误报「已请求配置」。

    旧实现走 cmd.exe + SW_HIDE 静默 bat,且忽略 122,即使用户取消 UAC 也提示
    「已请求配置崩溃重启策略」—— 即用户看到的「配置崩溃重启也没生效」。
    """
    monkeypatch.setattr(mcp_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(mcp_launcher, "is_service_installed", lambda: True)
    monkeypatch.setattr(mcp_launcher, "_shell_execute_elevated", lambda *a, **kw: 122)
    msg = mcp_launcher.service_configure_failure()
    assert "UAC" not in msg
    assert "取消" in msg


def test_service_configure_failure_blocks_when_service_not_installed(monkeypatch):
    """服务未注册时点「配置崩溃重启」应给出明确提示,而不是走 UAC。"""
    monkeypatch.setattr(mcp_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(mcp_launcher, "is_service_installed", lambda: False)
    escalated = []
    monkeypatch.setattr(
        mcp_launcher,
        "_shell_execute_elevated",
        lambda *a, **kw: escalated.append(a) or 99,
    )
    msg = mcp_launcher.service_configure_failure()
    assert "未注册" in msg
    assert escalated == [], "服务未注册时不应触发 UAC"

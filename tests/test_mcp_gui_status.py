import os
import socket
import subprocess
import time

import pytest

from src.services import mcp_heartbeat, mcp_launcher


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

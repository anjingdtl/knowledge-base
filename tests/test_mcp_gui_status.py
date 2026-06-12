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

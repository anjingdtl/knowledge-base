"""MCP 状态检测 — GUI 检测 MCP Server 是否可用"""
import socket
import time
from pathlib import Path

_path: Path | None = None


def _get_path() -> Path:
    global _path
    if _path is None:
        from src.utils.config import Config
        _path = Config.get_data_dir() / ".mcp_heartbeat"
    return _path


def beat():
    """记录一次 MCP 调用心跳"""
    _get_path().write_text(str(time.time()))


def is_mcp_available() -> bool:
    """判断 MCP Server 是否可用。

    检测策略：
    1. 心跳文件存在且 5 分钟内有过更新 → 可用
    2. 本地 MCP HTTP 端口可连接 → 可用
    3. 其他 → 不可用

    此函数由 GUI 主线程周期调用，禁止启动 wmic/sc 等外部进程。
    """
    elapsed = _seconds_since_last_beat()
    if elapsed is not None and elapsed < 300:
        return True

    return is_mcp_port_available()


def is_mcp_port_available() -> bool:
    """Probe the configured MCP TCP endpoint without consulting heartbeat data."""
    return _check_port()


def _check_port() -> bool:
    """快速探测本地 MCP HTTP 端口，不创建子进程。"""
    try:
        from src.utils.config import Config
        host = str(Config.get("mcp.bind_host", "127.0.0.1") or "127.0.0.1")
        port = int(Config.get("mcp.port", 9000) or 9000)
        with socket.create_connection((host, port), timeout=0.05):
            return True
    except (OSError, TypeError, ValueError):
        return False


def _seconds_since_last_beat() -> float | None:
    """返回距上次心跳的秒数，无心跳文件返回 None"""
    p = _get_path()
    if not p.exists():
        return None
    try:
        ts = float(p.read_text().strip())
        return time.time() - ts
    except (ValueError, OSError):
        return None


# 兼容旧接口
def seconds_since_last_beat() -> float | None:
    return _seconds_since_last_beat()

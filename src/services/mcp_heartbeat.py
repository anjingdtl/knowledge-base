"""MCP 状态检测 — GUI 检测 MCP Server 是否可用"""
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
    1. 有 mcp_server 进程在运行 → 可用
    2. 心跳文件存在且 5 分钟内有过更新 → 可用
    3. 其他 → 不可用
    """
    # 策略 1：检查进程
    if _check_process():
        return True

    # 策略 2：检查心跳时效
    elapsed = _seconds_since_last_beat()
    return elapsed is not None and elapsed < 300


def _check_process() -> bool:
    """检查是否有 mcp_server 进程在运行"""
    try:
        import subprocess
        result = subprocess.run(
            ["wmic", "process", "where",
             "commandline like '%run_mcp%' or commandline like '%mcp_server%'",
             "get", "processid"],
            capture_output=True, text=True, timeout=3,
        )
        lines = [l.strip() for l in result.stdout.strip().splitlines()
                 if l.strip() and l.strip() != "ProcessId"]
        return len(lines) > 0
    except Exception:
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

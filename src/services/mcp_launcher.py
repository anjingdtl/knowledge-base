"""MCP Server 子进程管理 — 从 GUI 一键启动/停止 MCP Server

MCP 以独立进程组运行，关闭 GUI 后 MCP 继续存活。
通过 PID 文件追踪进程状态，跨会话可用。
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_SCRIPT = _PROJECT_ROOT / "run_mcp.py"
_PID_FILE = _PROJECT_ROOT / "data" / "mcp.pid"

_process: subprocess.Popen | None = None


def _read_pid() -> int | None:
    """读取 PID 文件"""
    try:
        if _PID_FILE.exists():
            return int(_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        pass
    return None


def _write_pid(pid: int):
    """写入 PID 文件"""
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(pid))


def _remove_pid():
    """删除 PID 文件"""
    try:
        _PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _is_pid_alive(pid: int) -> bool:
    """检查指定 PID 的进程是否存活"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_process_windows(pid: int):
    """在 Windows 上可靠终止指定 PID 的进程。

    优先使用 taskkill /PID /F，对 DETACHED_PROCESS 创建的无控制台进程
    也能正确终止。失败时回退到 ctypes 调用 TerminateProcess。
    """
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    except OSError:
        pass

    # 回退：通过 ctypes 直接调用 Windows API
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError):
        raise ProcessLookupError(f"无法终止进程 {pid}")


def is_running() -> bool:
    """MCP 进程是否存活（支持跨会话检测）"""
    global _process
    # 先检查本会话的子进程
    if _process is not None and _process.poll() is None:
        return True
    # 再通过 PID 文件检查独立进程
    pid = _read_pid()
    if pid and _is_pid_alive(pid):
        return True
    return False


def start(host: str = "127.0.0.1", port: int = 9000) -> str:
    """启动 MCP Server 独立进程（streamable-http 模式）。

    关闭 GUI 后进程继续运行。

    Returns:
        启动结果描述文本
    """
    global _process
    if is_running():
        return "MCP Server 已在运行中"

    # 使用 pythonw.exe 避免 Windows 弹出终端窗口
    python_dir = Path(sys.executable).parent
    pythonw = python_dir / "pythonw.exe"
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    cmd = [str(pythonw), str(_MCP_SCRIPT), "-t", "streamable-http", "--host", host, "-p", str(port)]

    try:
        # CREATE_NEW_PROCESS_GROUP: 独立进程组，关闭 GUI 不影响
        # DETACHED_PROCESS: 脱离父进程控制台
        flags = 0
        if sys.platform == "win32":
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW

        _process = subprocess.Popen(
            cmd,
            cwd=str(_PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        _write_pid(_process.pid)
        time.sleep(1)
        if is_running():
            return f"MCP Server 已启动 (端口 {port})，关闭应用后将继续运行"
        else:
            _remove_pid()
            return f"MCP Server 启动失败（进程已退出）"
    except Exception as e:
        return f"MCP Server 启动失败: {e}"


def stop() -> str:
    """停止 MCP Server 进程。

    Returns:
        停止结果描述文本
    """
    global _process
    stopped = False

    # 先尝试停止本会话的子进程
    if _process is not None and _process.poll() is None:
        try:
            _process.terminate()
            _process.wait(timeout=5)
            stopped = True
        except subprocess.TimeoutExpired:
            _process.kill()
            stopped = True
        except Exception:
            pass
        _process = None

    # 再通过 PID 文件停止独立进程
    pid = _read_pid()
    if pid and _is_pid_alive(pid):
        try:
            if sys.platform == "win32":
                # Windows: 使用 taskkill /PID /F 可靠终止独立进程
                # DETACHED_PROCESS 进程没有控制台，无法接收 CTRL_BREAK_EVENT；
                # signal.SIGTERM 在 Windows 上不受原生支持。
                _kill_process_windows(pid)
            else:
                os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            if _is_pid_alive(pid):
                if sys.platform == "win32":
                    _kill_process_windows(pid)
                else:
                    os.kill(pid, signal.SIGKILL)
            stopped = True
        except (ProcessLookupError, PermissionError):
            pass

    _remove_pid()
    return "MCP Server 已停止" if stopped else "MCP Server 未在运行"

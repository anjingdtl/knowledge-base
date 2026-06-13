"""MCP Server 子进程管理 — 从 GUI 一键启动/停止 MCP Server

MCP 以独立进程组运行，关闭 GUI 后 MCP 继续存活。
通过 PID 文件追踪进程状态，跨会话可用。

支持两种模式:
- 进程模式: 直接启动子进程（默认）
- 服务模式: 通过 Windows 服务管理（ShineHeMCP）
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_SCRIPT = _PROJECT_ROOT / "run_mcp.py"
_PID_FILE = _PROJECT_ROOT / "data" / "mcp.pid"
_SERVICE_NAME = "ShineHeMCP"

_process: subprocess.Popen | None = None
_CREATE_NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
_CREATE_NEW_PROCESS_GROUP = int(
    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
)
_DETACHED_PROCESS = int(getattr(subprocess, "DETACHED_PROCESS", 0))


def _run_hidden(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run a helper command without flashing a console window on Windows."""
    if _is_windows():
        kwargs["creationflags"] = (
            kwargs.get("creationflags", 0) | _CREATE_NO_WINDOW
        )
    return subprocess.run(args, **kwargs)


# ---- Windows 服务模式 ----

def _is_windows() -> bool:
    return sys.platform == "win32"


def _shell_execute_elevated(
    executable: str,
    parameters: str,
) -> int:
    """Invoke ShellExecuteW without exposing Windows-only ctypes attributes."""
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        raise OSError("Windows ShellExecute API is unavailable")
    return int(
        windll.shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            parameters,
            str(_PROJECT_ROOT),
            0,
        )
    )


def is_service_installed() -> bool:
    """ShineHeMCP Windows 服务是否已注册"""
    if not _is_windows():
        return False
    try:
        result = _run_hidden(
            ["sc.exe", "query", _SERVICE_NAME],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_service_status() -> str:
    """获取 Windows 服务状态。

    Returns:
        'running' | 'stopped' | 'not_installed' | 'unknown'
    """
    if not _is_windows():
        return "not_installed"
    try:
        result = _run_hidden(
            ["sc.exe", "query", _SERVICE_NAME],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return "not_installed"
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("STATE"):
                if "RUNNING" in line:
                    return "running"
                elif "STOPPED" in line:
                    return "stopped"
        return "unknown"
    except Exception:
        return "unknown"


def get_service_failure_config() -> dict:
    """获取服务崩溃重启策略"""
    if not _is_windows():
        return {}
    try:
        result = _run_hidden(
            ["sc.exe", "qfailure", _SERVICE_NAME],
            capture_output=True, text=True, timeout=5,
        )
        info: dict[str, Any] = {
            "configured": False,
            "reset_period": 0,
            "actions": [],
        }
        for line in result.stdout.splitlines():
            line = line.strip()
            if "RESET_PERIOD" in line:
                info["reset_period"] = int(line.split(":")[-1].strip())
            elif "RESTART" in line:
                import re
                m = re.search(r"Delay\s*=\s*(\d+)", line)
                delay = int(m.group(1)) if m else 0
                info["actions"].append({"type": "restart", "delay_ms": delay})
        info["configured"] = len(info["actions"]) > 0
        return info
    except Exception:
        return {"configured": False}


def service_start() -> str:
    """启动 Windows 服务（需要管理员权限）"""
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        # 先检查服务是否已在运行
        if get_service_status() == "running":
            return "服务已在运行中"
        result = _run_hidden(
            ["net", "start", _SERVICE_NAME],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return "Windows 服务已启动"
        # 可能需要管理员权限
        if "拒绝访问" in result.stderr or "Access is denied" in result.stderr:
            return "启动失败：需要管理员权限"
        return f"启动失败：{result.stderr.strip() or result.stdout.strip()}"
    except Exception as e:
        return f"启动异常: {e}"


def service_stop() -> str:
    """停止 Windows 服务"""
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        if get_service_status() != "running":
            return "服务未在运行"
        result = _run_hidden(
            ["net", "stop", _SERVICE_NAME],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return "Windows 服务已停止"
        if "拒绝访问" in result.stderr or "Access is denied" in result.stderr:
            return "停止失败：需要管理员权限"
        return f"停止失败：{result.stderr.strip() or result.stdout.strip()}"
    except Exception as e:
        return f"停止异常: {e}"


def service_restart() -> str:
    """重启 Windows 服务"""
    msg = service_stop()
    if "失败" in msg or "异常" in msg:
        return msg
    time.sleep(2)
    return service_start()


def service_install() -> str:
    """注册 Windows 服务（需管理员权限）

    通过 UAC 提权执行 python windows_service.py install
    """
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        script = _PROJECT_ROOT / "windows_service.py"
        # 使用 ShellExecute 以管理员身份运行
        ret = _shell_execute_elevated(
            sys.executable,
            f'"{script}" install',
        )
        if ret <= 32:
            return f"提权失败（返回值 {ret}），请手动以管理员身份运行: python windows_service.py install"
        time.sleep(3)
        if is_service_installed():
            return "Windows 服务注册成功"
        return "服务注册可能未成功，请检查 UAC 是否已确认"
    except Exception as e:
        return f"注册异常: {e}"


def service_remove() -> str:
    """卸载 Windows 服务（需管理员权限）"""
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        # 先停止
        if get_service_status() == "running":
            service_stop()
            time.sleep(2)
        script = _PROJECT_ROOT / "windows_service.py"
        ret = _shell_execute_elevated(
            sys.executable,
            f'"{script}" remove',
        )
        if ret <= 32:
            return "提权失败，请手动以管理员身份运行: python windows_service.py remove"
        time.sleep(3)
        if not is_service_installed():
            return "Windows 服务已卸载"
        return "服务卸载可能未成功，请检查 UAC 是否已确认"
    except Exception as e:
        return f"卸载异常: {e}"


def service_configure_failure() -> str:
    """配置服务崩溃自动重启策略（需管理员权限）"""
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        # 通过 bat 脚本以管理员身份执行 sc failure
        bat_content = '@echo off\nsc failure ShineHeMCP reset= 86400 actions= restart/5000/restart/10000/restart/30000\n'
        bat_path = _PROJECT_ROOT / "data" / "_set_failure.bat"
        bat_path.parent.mkdir(parents=True, exist_ok=True)
        bat_path.write_text(bat_content, encoding="ascii")
        ret = _shell_execute_elevated(
            "cmd.exe",
            f'/c "{bat_path}"',
        )
        if ret <= 32:
            return "提权失败，请手动以管理员身份运行: sc.exe failure ShineHeMCP reset= 86400 actions= restart/5000/restart/10000/restart/30000"
        time.sleep(3)
        fc = get_service_failure_config()
        if fc.get("configured"):
            return "崩溃重启策略配置成功（5s/10s/30s 自动重启）"
        return "策略配置可能未成功，请检查 UAC 是否已确认"
    except Exception as e:
        return f"配置异常: {e}"


# ---- 进程模式（原有逻辑）----


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


def _pid_matches_mcp(pid: int) -> bool:
    """Confirm a PID file still points to this project's MCP process."""
    if not _is_pid_alive(pid):
        return False
    try:
        if _is_windows():
            result = _run_hidden(
                [
                    "wmic", "process", "where", f"processid={pid}",
                    "get", "commandline", "/value",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
            command_line = result.stdout.lower()
        else:
            command_line = (
                Path(f"/proc/{pid}/cmdline")
                .read_text(encoding="utf-8", errors="ignore")
                .lower()
            )
        return "run_mcp.py" in command_line or "windows_service.py" in command_line
    except (OSError, subprocess.TimeoutExpired):
        return False


def _kill_process_windows(pid: int):
    """在 Windows 上可靠终止指定 PID 的进程。

    优先使用 taskkill /PID /F，对 DETACHED_PROCESS 创建的无控制台进程
    也能正确终止。失败时回退到 ctypes 调用 TerminateProcess。
    """
    try:
        _run_hidden(
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
    """MCP 是否存活；周期调用路径不启动外部命令。"""
    global _process
    if _process is not None and _process.poll() is None:
        return True

    from src.services.mcp_heartbeat import is_mcp_available
    if is_mcp_available():
        return True

    # 不信任陈旧 PID；实际服务应由心跳或端口证明存活。
    pid = _read_pid()
    if pid:
        _remove_pid()
    return False


def start(host: str = "127.0.0.1", port: int = 9000) -> str:
    """启动 MCP Server。

    如果 Windows 服务已注册，自动走服务模式启动；
    否则走子进程模式。

    Returns:
        启动结果描述文本
    """
    global _process
    if is_running():
        return "MCP Server 已在运行中"

    # Windows 服务模式
    if _is_windows() and is_service_installed():
        return service_start()

    # 子进程模式

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
            flags = (
                _CREATE_NEW_PROCESS_GROUP
                | _DETACHED_PROCESS
                | _CREATE_NO_WINDOW
            )

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
            return "MCP Server 启动失败（进程已退出）"
    except Exception as e:
        return f"MCP Server 启动失败: {e}"


def stop() -> str:
    """停止 MCP Server。

    如果 Windows 服务已注册且正在运行，走服务模式停止；
    否则走子进程模式。

    Returns:
        停止结果描述文本
    """
    global _process

    # Windows 服务模式
    if _is_windows() and is_service_installed() and get_service_status() == "running":
        return service_stop()

    # 子进程模式
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
    if pid and _pid_matches_mcp(pid):
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

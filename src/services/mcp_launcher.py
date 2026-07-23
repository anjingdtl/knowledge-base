"""MCP Server 子进程管理 — 从 GUI 一键启动/停止 MCP Server

MCP 以独立进程组运行，关闭 GUI 后 MCP 继续存活。
通过 PID 文件追踪进程状态，跨会话可用。

支持两种模式:
- 进程模式: 直接启动子进程（默认）
- 服务模式: 通过 Windows 服务管理（ShineHeMCP）
"""
import logging
import os
import signal
import subprocess
import sys
import time
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from src.storage.database_bootstrap import inspect_database_bootstrap
from src.storage.migration_cli import migrate_database
from src.utils.config import Config

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_SCRIPT = _PROJECT_ROOT / "run_mcp.py"
_PID_FILE = _PROJECT_ROOT / "data" / "mcp.pid"
_STARTUP_LOG_FILE = _PROJECT_ROOT / "data" / "logs" / "mcp-startup.log"
_SERVICE_NAME = "ShineHeMCP"
_SERVICE_PORT = 9000  # windows_service.py 硬编码的服务监听端口

_process: subprocess.Popen | None = None
_process_log_handle = None
_CREATE_NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
_CREATE_NEW_PROCESS_GROUP = int(
    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
)
_DETACHED_PROCESS = int(getattr(subprocess, "DETACHED_PROCESS", 0))
_SUCCESSFUL_START_PREFIXES = (
    "MCP Server 已启动",
    "MCP Server 已在运行中",
    "服务已在运行中",
)


def get_migration_requirement() -> str | None:
    """Return the database migration reason that blocks MCP startup, if any.

    The inspection is read-only, so this can safely run from the GUI before the
    user confirms the backup-first migration workflow.
    """
    plan = inspect_database_bootstrap(
        Config.get_db_path(),
        config=Config,
        project_root=_PROJECT_ROOT,
    )
    if plan.action != "block":
        return None
    return f"MCP 启动前需要数据库迁移：{plan.migration_status.message}"


def migrate_database_for_mcp() -> str:
    """Run the existing backup-first migration workflow for the configured DB."""
    result = migrate_database(Config.get_db_path())
    if not result.get("ok"):
        raise RuntimeError("数据库迁移未完成")
    backup = result.get("backup")
    if not isinstance(backup, str) or not backup.strip():
        raise RuntimeError("数据库迁移未完成：未返回可用备份路径")
    return f"数据库已安全迁移，备份：{backup}"


def is_start_success_message(message: str) -> bool:
    """Whether a launcher result is one of the explicit successful outcomes.

    ``start()`` returns user-facing strings rather than a typed result.  Use a
    conservative allowlist so errors such as UAC elevation rejection are never
    mistaken for a running MCP server.
    """
    return message.startswith(_SUCCESSFUL_START_PREFIXES)


def is_start_pending_message(message: str) -> bool:
    """Whether the launcher accepted a service start request awaiting UAC/ready."""
    return message.startswith("已请求启动服务")


def _run_hidden(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run a helper command without flashing a console window on Windows."""
    if _is_windows():
        kwargs["creationflags"] = (
            kwargs.get("creationflags", 0) | _CREATE_NO_WINDOW
        )
    # Windows helpers (wmic/sc/powershell) may emit system code-page bytes.
    # Always decode with replacement so reader threads never raise UnicodeDecodeError.
    if kwargs.get("text") or kwargs.get("universal_newlines"):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
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


def is_service_registration_valid() -> bool:
    """Whether the registered service can load its Python service class.

    ``sc query`` only proves that a service name exists.  A partially removed
    or old packaged installation can leave that entry behind without the
    pywin32 ``Parameters\\PythonClass`` value.  Sending the sidebar button to
    such a service merely opens UAC and then leaves MCP offline.  Treat it as
    an unavailable optional service and let the normal child-process launcher
    handle the request instead.
    """
    if not _is_windows() or not is_service_installed():
        return False
    try:
        result = _run_hidden(
            [
                "reg.exe",
                "query",
                rf"HKLM\\SYSTEM\\CurrentControlSet\\Services\\{_SERVICE_NAME}\\Parameters",
                "/v",
                "PythonClass",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
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


def _parse_failure_config(text: str) -> dict:
    """解析 sc qfailure 输出文本,兼容中英文本地化。

    sc.exe 的字段名(FAILURE_ACTIONS / RESET_PERIOD)固定英文,但 failure action
    的描述文本(RESTART -- Delay = N milliseconds)在非英文 Windows 上被本地化
    (中文: 「重新启动 -- 延迟 = N 毫秒」)。旧实现按行匹配 "RESTART" / "Delay"
    关键词,中文输出下永远命中失败,导致 UI 始终显示「未配置」。

    本函数不依赖本地化关键词:靠字段名 FAILURE_ACTIONS 定位 actions 区块后,
    直接提取其中的延迟数值(= 后的毫秒数)。
    """
    import re

    info: dict[str, Any] = {
        "configured": False,
        "reset_period": 0,
        "actions": [],
    }
    # RESET_PERIOD 字段名英文固定;冒号前的描述(如 "in seconds" / "秒数")任意
    m = re.search(r"RESET_PERIOD[^:\n]*:\s*(\d+)", text)
    if m:
        info["reset_period"] = int(m.group(1))
    # FAILURE_ACTIONS 字段名英文固定;其后(同行剩余 + 续行)是 action 描述,
    # 续行以空白开头、不是 "KEY:" 格式。lookahead 遇到下一个字段或文本结尾即停止。
    m = re.search(
        r"FAILURE_ACTIONS\s*:(.*?)(?=\n\s*[A-Z_]+\s*:|\Z)",
        text,
        re.DOTALL,
    )
    if m:
        for delay in re.findall(r"=\s*(\d+)", m.group(1)):
            info["actions"].append({"type": "restart", "delay_ms": int(delay)})
    info["configured"] = len(info["actions"]) > 0
    return info


def get_service_failure_config() -> dict:
    """获取服务崩溃重启策略。

    Returns:
        {'configured': bool, 'reset_period': 秒, 'actions': [{'delay_ms': 毫秒}]}
    """
    if not _is_windows():
        return {}
    try:
        result = _run_hidden(
            ["sc.exe", "qfailure", _SERVICE_NAME],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {"configured": False, "reset_period": 0, "actions": []}
        return _parse_failure_config(result.stdout)
    except Exception:
        return {"configured": False}


def _port_in_use(port: int) -> int | None:
    """返回监听指定端口的 PID;无人监听返回 None。

    服务与子进程模式都监听 _SERVICE_PORT。若服务启动前该端口已被占用
    (通常是子进程模式的 MCP 仍在运行),uvicorn bind 会失败,服务启动即崩溃,
    表现为「点启动后状态仍是已停止」。
    """
    try:
        result = _run_hidden(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "LISTENING" not in line:
                continue
            if any(tok.endswith(f":{port}") for tok in line.split()):
                return int(line.split()[-1])
    except Exception:
        pass
    return None


def service_start() -> str:
    """启动 Windows 服务（通过 UAC 提权）"""
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        # 先检查服务是否已在运行
        if get_service_status() == "running":
            return "服务已在运行中"
        # 端口冲突预检:服务监听 _SERVICE_PORT,被占用则 bind 必失败
        holder = _port_in_use(_SERVICE_PORT)
        if holder is not None:
            return (
                f"端口 {_SERVICE_PORT} 已被进程 PID {holder} 占用,服务无法绑定启动。\n"
                f"(通常是子进程模式的 MCP 仍在运行,请先在侧边栏停止它再启动服务)"
            )
        # 通过 ShellExecuteW "runas" 触发 UAC 提权
        ret = _shell_execute_elevated("sc.exe", f"start {_SERVICE_NAME}")
        if ret <= 32:
            return f"提权失败（返回值 {ret}），请手动以管理员身份运行: sc start {_SERVICE_NAME}"
        return "已请求启动服务，请确认 UAC 弹窗"
    except Exception as e:
        return f"启动异常: {e}"


def service_stop() -> str:
    """停止 Windows 服务（通过 UAC 提权）"""
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        if get_service_status() != "running":
            return "服务未在运行"
        # 通过 ShellExecuteW "runas" 触发 UAC 提权
        ret = _shell_execute_elevated("sc.exe", f"stop {_SERVICE_NAME}")
        if ret <= 32:
            return f"提权失败（返回值 {ret}），请手动以管理员身份运行: sc stop {_SERVICE_NAME}"
        return "已请求停止服务，请确认 UAC 弹窗"
    except Exception as e:
        return f"停止异常: {e}"


def service_restart() -> str:
    """重启 Windows 服务（通过 UAC 提权，一个弹窗完成停止+启动）"""
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        # 用 bat 脚本实现 stop→wait→start，只需一次 UAC 提权
        bat_content = (
            '@echo off\n'
            f'sc stop {_SERVICE_NAME}\n'
            'timeout /t 3 /nobreak >nul\n'
            f'sc start {_SERVICE_NAME}\n'
        )
        bat_path = _PROJECT_ROOT / "data" / "_restart_svc.bat"
        bat_path.parent.mkdir(parents=True, exist_ok=True)
        bat_path.write_text(bat_content, encoding="ascii")
        ret = _shell_execute_elevated("cmd.exe", f'/c "{bat_path}"')
        if ret <= 32:
            return f"提权失败（返回值 {ret}），请手动以管理员身份运行重启操作"
        return "已请求重启服务，请确认 UAC 弹窗"
    except Exception as e:
        return f"重启异常: {e}"


def service_install() -> str:
    """注册 Windows 服务（通过 UAC 提权）

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
        return "已请求注册服务，请确认 UAC 弹窗"
    except Exception as e:
        return f"注册异常: {e}"


def service_remove() -> str:
    """卸载 Windows 服务（通过 UAC 提权）"""
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        # 如果服务正在运行，先触发 UAC 停止
        if get_service_status() == "running":
            _shell_execute_elevated("sc.exe", f"stop {_SERVICE_NAME}")
            time.sleep(3)
        script = _PROJECT_ROOT / "windows_service.py"
        ret = _shell_execute_elevated(
            sys.executable,
            f'"{script}" remove',
        )
        if ret <= 32:
            return "提权失败，请手动以管理员身份运行: python windows_service.py remove"
        return "已请求卸载服务，请确认 UAC 弹窗"
    except Exception as e:
        return f"卸载异常: {e}"


def service_configure_failure() -> str:
    """配置服务崩溃自动重启策略（通过 UAC 提权）"""
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        # 通过 bat 脚本以管理员身份执行 sc failure
        bat_content = (
            '@echo off\n'
            f'sc failure {_SERVICE_NAME} reset= 86400 actions= restart/5000/restart/10000/restart/30000\n'
        )
        bat_path = _PROJECT_ROOT / "data" / "_set_failure.bat"
        bat_path.parent.mkdir(parents=True, exist_ok=True)
        bat_path.write_text(bat_content, encoding="ascii")
        ret = _shell_execute_elevated("cmd.exe", f'/c "{bat_path}"')
        if ret <= 32:
            return "提权失败，请手动以管理员身份运行: sc failure ShineHeMCP reset= 86400 actions= restart/5000/restart/10000/restart/30000"
        return "已请求配置崩溃重启策略，请确认 UAC 弹窗"
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


def _wmic_commandline(pid: int) -> str:
    """通过 wmic 读取进程命令行(老版 Windows)。

    返回空串表示不可用:wmic 未安装(Win11 24H2+ 默认移除)、查询失败或无输出。
    抛 OSError/TimeoutExpired 由上层 _read_process_commandline 捕获并回退。
    """
    result = _run_hidden(
        ["wmic", "process", "where", f"processid={pid}",
         "get", "commandline", "/value"],
        capture_output=True, text=True, timeout=3,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    return result.stdout


def _cim_commandline(pid: int) -> str:
    """通过 PowerShell Get-CimInstance 读取进程命令行(wmic 的现代替代)。

    Win7+/PowerShell 3.0+ 内置,是 Win11 24H2+ 移除 wmic 后的回退路径。
    """
    result = _run_hidden(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
        capture_output=True, text=True, timeout=5,
    )
    return result.stdout


def _read_process_commandline(pid: int) -> str:
    """读取进程命令行并转小写。

    Windows 上 wmic 优先(快),不可用或失败时回退 PowerShell CIM。
    两个探测点都拿不到有效输出则返回空串。
    """
    if _is_windows():
        for probe in (_wmic_commandline, _cim_commandline):
            try:
                out = probe(pid)
                if out.strip():
                    return out.lower()
            except (OSError, subprocess.TimeoutExpired):
                continue
        return ""
    return (
        Path(f"/proc/{pid}/cmdline")
        .read_text(encoding="utf-8", errors="ignore")
        .lower()
    )


def _pid_matches_mcp(pid: int) -> bool:
    """Confirm a PID file still points to this project's MCP process."""
    if not _is_pid_alive(pid):
        return False
    try:
        command_line = _read_process_commandline(pid)
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
    """Whether the GUI-managed HTTP MCP process is running.

    A heartbeat can come from a previous, already-exited process (or a stdio
    MCP client), so it must not prevent the GUI from launching its HTTP server.
    Only the tracked child process or the configured HTTP port is authoritative
    for this launcher.
    """
    global _process
    if _process is not None and _process.poll() is None:
        return True
    if _process is not None:
        _close_process_log()
        _process = None

    from src.services.mcp_heartbeat import is_mcp_port_available
    if is_mcp_port_available():
        return True

    # 不信任陈旧 PID；实际服务应由受管进程或 TCP 端口证明存活。
    pid = _read_pid()
    if pid:
        _remove_pid()
    return False


def _startup_error_summary() -> str:
    """Return the last useful launcher error without exposing a full traceback."""
    try:
        lines = _STARTUP_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "未记录到错误日志"
    useful = [line.strip() for line in lines if line.strip()]
    if not useful:
        return "未记录到错误日志"
    return " ".join(useful[-3:])[:500]


def get_startup_diagnostic() -> str:
    """A concise MCP launch diagnostic suitable for the GUI."""
    return f"启动日志：{_STARTUP_LOG_FILE}\n最近输出：{_startup_error_summary()}"


def _close_process_log() -> None:
    global _process_log_handle
    if _process_log_handle is not None:
        try:
            _process_log_handle.close()
        except OSError:
            pass
        _process_log_handle = None


def _resolve_mcp_python() -> Path:
    """Prefer the interpreter that has the MCP dependency installed.

    During source development the GUI may be opened with a system Python while
    project dependencies live in ``.venv``.  Starting ``run_mcp.py`` with the
    former makes the subprocess exit immediately with ``No module named
    fastmcp``.  The project virtual environment is a safe fallback.
    """
    if find_spec("fastmcp") is not None:
        python_dir = Path(sys.executable).parent
        python = python_dir / "python.exe"
        return python if python.exists() else Path(sys.executable)

    venv_scripts = _PROJECT_ROOT / ".venv" / "Scripts"
    for filename in ("python.exe", "pythonw.exe"):
        candidate = venv_scripts / filename
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def start(host: str | None = None, port: int | None = None) -> str:
    """启动 MCP Server。

    如果 Windows 服务已注册且配置完整，自动走服务模式启动；
    已残留但配置不完整的服务会自动回退到子进程模式。

    Returns:
        启动结果描述文本
    """
    global _process, _process_log_handle
    host = host or str(Config.get("mcp.bind_host", "127.0.0.1") or "127.0.0.1")
    port = int(port if port is not None else (Config.get("mcp.port", 9000) or 9000))
    if is_running():
        return "MCP Server 已在运行中"

    # Windows 服务模式
    if _is_windows() and is_service_installed():
        if is_service_registration_valid():
            return service_start()
        # A broken pywin32 registration must not make the ordinary GUI button
        # unusable.  The settings page can still be used to remove/reinstall
        # the service when the user wants the auto-start service mode.
        logger.warning(
            "ShineHeMCP service is registered without PythonClass; "
            "falling back to the GUI-managed MCP process"
        )

    # 子进程模式

    # CREATE_NO_WINDOW 会隐藏 python.exe 的控制台，同时保留 stderr 以记录启动失败原因。
    python_executable = _resolve_mcp_python()
    cmd = [str(python_executable), str(_MCP_SCRIPT), "-t", "streamable-http", "--host", host, "-p", str(port)]

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

        _STARTUP_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _process_log_handle = _STARTUP_LOG_FILE.open("w", encoding="utf-8")
        _process = subprocess.Popen(
            cmd,
            cwd=str(_PROJECT_ROOT),
            stdout=_process_log_handle,
            stderr=_process_log_handle,
            creationflags=flags,
        )
        _write_pid(_process.pid)
        time.sleep(1)
        if is_running():
            return f"MCP Server 已启动 (端口 {port})，关闭应用后将继续运行"
        else:
            _remove_pid()
            detail = _startup_error_summary()
            return f"MCP Server 启动失败（进程已退出）：{detail}\n日志：{_STARTUP_LOG_FILE}"
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
        _close_process_log()

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
    _close_process_log()
    return "MCP Server 已停止" if stopped else "MCP Server 未在运行"

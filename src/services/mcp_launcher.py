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
# 不再使用 DETACHED_PROCESS:它与 CREATE_NO_WINDOW 互斥(MSDN 明确规定),
# 同时设置时 CREATE_NO_WINDOW 会被忽略,导致子进程新建可见控制台窗口。

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


# ShellExecuteW 返回值约定:返回值 > 32 表示调用本身成功触发(并不保证后续命令成功),
# <= 32 表示触发失败。但 122 (ERROR_CANCELLED) 是个特例 —— 它 > 32 但代表用户在 UAC
# 弹窗中点了「否」,根本没执行提权命令。旧代码仅检查 <= 32,把 122 当作成功,
# 导致用户点了「启动服务」但取消了 UAC 后,GUI 仍提示「已请求启动服务」,并开始 8 秒
# 轮询,最后才弹出超时排查 —— 即用户看到的「点了启动服务后状态一直是已停止」。
_ERROR_CANCELLED = 122


def _is_elevation_failed_or_cancelled(ret: int) -> bool:
    """ShellExecuteW runas 是否真的失败了。

    <= 32 是常规失败;122 是 ERROR_CANCELLED(用户在 UAC 弹窗点了「否」)。
    两者都视为「提权未发生」,调用方应当直接返回失败提示而非开始轮询。
    """
    return ret <= 32 or ret == _ERROR_CANCELLED


def _elevation_failure_reason(ret: int) -> str:
    """把 ShellExecuteW 错误码翻译成用户可读的中文提示。

    重要:不能出现 "UAC" 字样 —— GUI 通过 ``"UAC" in msg`` 判定「操作已挂起、
    等待用户在 UAC 弹窗中确认」并启动轮询。失败消息出现 "UAC" 会让取消/失败
    也误入轮询分支,显示「服务启动未生效」等错误排查提示,体验混乱。
    使用「提权」一词替代。
    """
    if ret == _ERROR_CANCELLED:
        return "提权被取消(用户在弹窗中点了「否」)"
    if ret == 0:
        return "内存不足或 shell 不可用"
    if ret == 2:
        return "可执行文件未找到"
    if ret == 3:
        return "路径未找到"
    if ret == 5:
        return "拒绝访问"
    if ret == 8:
        return "内存不足"
    return f"返回值 {ret}"


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
    pywin32 ``PythonClass`` registry key.  Sending the sidebar button to
    such a service merely opens UAC and then leaves MCP offline.  Treat it as
    an unavailable optional service and let the normal child-process launcher
    handle the request instead.
    """
    if not _is_windows() or not is_service_installed():
        return False
    try:
        # v1.11.1+ uses the small native .NET service dispatcher.  It avoids
        # pywin32/pythonservice.exe failing before it can report SERVICE_RUNNING
        # when a virtual environment lacks the base Python DLL.
        config = _run_hidden(
            ["sc.exe", "qc", _SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if (
            config.returncode == 0
            and "ShineHeMCPServiceHost.exe" in config.stdout
        ):
            return True
        # pywin32 把服务类写为独立子键的默认值：
        # HKLM\System\CurrentControlSet\Services\ShineHeMCP\PythonClass。
        # 此前查询了错误的 Parameters 子键，令每个正常服务也被误判无效，
        # 侧边栏因此启动子进程并与服务争抢 9000 端口。
        result = _run_hidden(
            [
                "reg.exe",
                "query",
                f"HKLM\\SYSTEM\\CurrentControlSet\\Services\\{_SERVICE_NAME}\\PythonClass",
                "/ve",
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
        if not is_service_installed():
            return "服务未注册，请先点「注册为 Windows 服务」"
        if not is_service_registration_valid():
            return (
                "Windows 服务注册不完整，缺少 pywin32 的 PythonClass 服务类信息，"
                "无法启动。\n"
                "请在「服务管理」中点击「修复 Windows 服务」，完成提权后再启动。"
            )
        # 先检查服务是否已在运行
        if get_service_status() == "running":
            return "服务已在运行中"
        # 数据库迁移预检:服务启动后会通过 lifespan 触发 startup_gate,若 schema 落后
        # 于 head 会抛 MigrationGateError 让 uvicorn 立即退出 —— 表现为「点启动后状态
        # 一直是已停止」。在 UAC 之前先做只读检查,给用户清晰的迁移提示。
        migration_required = get_migration_requirement()
        if migration_required:
            return (
                f"{migration_required}\n"
                "请先在侧边栏点「启动 MCP」走子进程模式完成自动迁移,"
                "或在命令行执行 alembic upgrade head 后再启动服务。"
            )
        # 端口冲突预检:服务监听 _SERVICE_PORT,被占用则 bind 必失败
        holder = _port_in_use(_SERVICE_PORT)
        if holder is not None:
            return (
                f"端口 {_SERVICE_PORT} 已被进程 PID {holder} 占用,服务无法绑定启动。\n"
                f"(通常是子进程模式的 MCP 仍在运行,请先在侧边栏停止它再启动服务)"
            )
        # 通过 ShellExecuteW "runas" 触发 UAC 提权
        ret = _shell_execute_elevated("sc.exe", f"start {_SERVICE_NAME}")
        if _is_elevation_failed_or_cancelled(ret):
            # 关键修复:旧版仅判断 ret<=32,忽略 122 (ERROR_CANCELLED),
            # 导致用户取消 UAC 后 GUI 仍提示「已请求启动服务」并启动 8 秒轮询,
            # 最终才弹超时排查 —— 即「服务状态一直是已停止」表象。
            return (
                f"启动未执行:{_elevation_failure_reason(ret)}。\n"
                f"如需手动以管理员身份运行: sc start {_SERVICE_NAME}"
            )
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
        if _is_elevation_failed_or_cancelled(ret):
            return (
                f"停止未执行:{_elevation_failure_reason(ret)}。\n"
                f"如需手动以管理员身份运行: sc stop {_SERVICE_NAME}"
            )
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
        if _is_elevation_failed_or_cancelled(ret):
            return (
                f"重启未执行:{_elevation_failure_reason(ret)}。\n"
                f"如需手动以管理员身份运行:先 sc stop {_SERVICE_NAME},"
                f"再 sc start {_SERVICE_NAME}"
            )
        return "已请求重启服务，请确认 UAC 弹窗"
    except Exception as e:
        return f"重启异常: {e}"


def service_install() -> str:
    """注册或更新由原生服务宿主承载的 Windows 服务（通过 UAC 提权）。"""
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        script = _PROJECT_ROOT / "scripts" / "register_windows_service_host.ps1"
        # 原先直接注册 pywin32 的 pythonservice.exe。该可执行文件在 venv
        # 根目录无法加载 pythonXY.dll 时会在服务类导入前触发 SCM 7009。改用
        # 原生服务分发器承载 MCP 子进程，启动后立即向 SCM 报告 RUNNING。
        ret = _shell_execute_elevated(
            "powershell.exe",
            f'-NoProfile -ExecutionPolicy Bypass -File "{script}"',
        )
        if _is_elevation_failed_or_cancelled(ret):
            return (
                f"注册未执行:{_elevation_failure_reason(ret)}。\n"
                "如需手动以管理员身份运行: "
                "powershell -ExecutionPolicy Bypass -File "
                "scripts/register_windows_service_host.ps1"
            )
        return "已请求注册/修复服务，请确认 UAC 弹窗"
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
            str(_resolve_service_python()),
            f'"{script}" remove',
        )
        if _is_elevation_failed_or_cancelled(ret):
            return (
                f"卸载未执行:{_elevation_failure_reason(ret)}。\n"
                f"如需手动以管理员身份运行: python windows_service.py remove"
            )
        return "已请求卸载服务，请确认 UAC 弹窗"
    except Exception as e:
        return f"卸载异常: {e}"


def service_configure_failure() -> str:
    """配置服务崩溃自动重启策略（通过 UAC 提权）"""
    if not _is_windows():
        return "仅支持 Windows 服务模式"
    try:
        if not is_service_installed():
            return "服务未注册，请先点「注册为 Windows 服务」"
        # 直接以管理员身份执行 sc.exe,无需 cmd.exe + bat 中间层。
        # 旧实现用 SW_HIDE 的 cmd.exe 静默执行 bat —— 若 bat 中 sc failure 因
        # 任何原因失败,用户无任何反馈,UI 后续 qfailure 查询仍是「未配置」,
        # 即「配置崩溃重启也没生效」表象。
        # 直接调用 sc.exe 后:ShellExecuteW 触发 UAC,确认后 sc.exe 自己执行
        # (无中间 cmd 进程,更直接、错误码更清晰)。
        ret = _shell_execute_elevated(
            "sc.exe",
            f"failure {_SERVICE_NAME} reset= 86400 "
            f"actions= restart/5000/restart/10000/restart/30000",
        )
        if _is_elevation_failed_or_cancelled(ret):
            return (
                f"配置未执行:{_elevation_failure_reason(ret)}。\n"
                f"如需手动以管理员身份运行: sc failure {_SERVICE_NAME} "
                f"reset= 86400 actions= restart/5000/restart/10000/restart/30000"
            )
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

    优先选择 ``pythonw.exe`` (无控制台子系统的 GUI 子系统可执行文件) — 即便
    漏掉 ``CREATE_NO_WINDOW`` 标志也不会弹出终端窗口。
    ``run_mcp.py`` 顶部已对 ``pythonw.exe`` 的 None stdout/stderr 做了 devnull 兜底。
    """
    if find_spec("fastmcp") is not None:
        python_dir = Path(sys.executable).parent
        # 优先 pythonw.exe:无控制台子系统,任何场景都不会弹窗
        pythonw = python_dir / "pythonw.exe"
        if pythonw.exists():
            return pythonw
        python = python_dir / "python.exe"
        return python if python.exists() else Path(sys.executable)

    venv_scripts = _PROJECT_ROOT / ".venv" / "Scripts"
    # 同样优先 pythonw.exe
    for filename in ("pythonw.exe", "python.exe"):
        candidate = venv_scripts / filename
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def _resolve_service_python() -> Path:
    """Return an interpreter that can execute the pywin32 service installer.

    The GUI may be launched with a global Python that has FastMCP but not
    pywin32.  In that case using ``sys.executable`` makes the elevated install
    command fail at ``import servicemanager`` and leaves the old service in
    place.  The project virtual environment is the packaged service runtime.
    """
    venv_python = _PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return venv_python
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
        # CREATE_NEW_PROCESS_GROUP: 独立进程组,关闭 GUI 不影响子进程
        # CREATE_NO_WINDOW: 不为子进程分配控制台窗口
        # NOTE: 不要叠加 _DETACHED_PROCESS —— MSDN 明确 CREATE_NO_WINDOW
        # 在与 DETACHED_PROCESS 同用时会被忽略,反而导致子进程新建可见控制台
        # 窗口(即用户在 GUI 里点「启动 MCP」时看到的终端弹窗)。
        # pythonw.exe (在 _resolve_mcp_python 中优先) 本身就无控制台子系统,
        # 与 CREATE_NO_WINDOW 组合可彻底杜绝任何终端闪现。
        flags = 0
        if sys.platform == "win32":
            flags = _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW

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

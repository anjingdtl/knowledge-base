"""MCP Streamable HTTP 服务独立管理脚本

用法:
    python scripts/mcp_service.py start    # 启动 MCP HTTP 服务（后台常驻）
    python scripts/mcp_service.py stop     # 停止 MCP 服务
    python scripts/mcp_service.py status   # 查看服务状态
    python scripts/mcp_service.py restart  # 重启服务

特点:
    - 以独立进程运行，关闭 GUI/终端不影响服务
    - 通过 PID 文件追踪进程状态
    - 默认监听 127.0.0.1:9000
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Windows 终端中文编码兼容
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MCP_SCRIPT = _PROJECT_ROOT / "run_mcp.py"
_PID_FILE = _PROJECT_ROOT / "data" / "mcp.pid"
_LOG_FILE = _PROJECT_ROOT / "data" / "mcp_service.log"

# 默认配置
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 9000


def _read_pid() -> int | None:
    try:
        if _PID_FILE.exists():
            return int(_PID_FILE.read_text().strip().splitlines()[0])
    except (ValueError, OSError, IndexError):
        pass
    return None


def _read_pid_meta() -> tuple[int | None, str, int]:
    """读取 PID 文件中的 PID、host、port"""
    try:
        if _PID_FILE.exists():
            lines = _PID_FILE.read_text().strip().splitlines()
            pid = int(lines[0]) if lines else None
            host = lines[1] if len(lines) > 1 else _DEFAULT_HOST
            port = int(lines[2]) if len(lines) > 2 else _DEFAULT_PORT
            return pid, host, port
    except (ValueError, OSError, IndexError):
        pass
    return None, _DEFAULT_HOST, _DEFAULT_PORT


def _write_pid(pid: int, host: str = "", port: int = 0):
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(f"{pid}\n{host}\n{port}")


def _remove_pid():
    try:
        _PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_windows(pid: int):
    """Windows 上可靠终止进程"""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0001, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 1)
                kernel32.CloseHandle(handle)
        except (OSError, AttributeError):
            raise ProcessLookupError(f"无法终止进程 {pid}")


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """检查端口是否被占用"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def do_status() -> int:
    """查看服务状态"""
    pid, host, port = _read_pid_meta()
    if pid and _is_pid_alive(pid):
        print(f"[OK] MCP 服务运行中 (PID: {pid}, 端口: {port})")
        if _is_port_in_use(port, host):
            print(f"  HTTP 接入: http://{host}:{port}/mcp")
        return 0
    else:
        if pid:
            _remove_pid()
        print("[FAIL] MCP 服务未运行")
        return 1


def do_start(host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> int:
    """启动 MCP 服务"""
    # 检查是否已运行
    pid = _read_pid()
    if pid and _is_pid_alive(pid):
        print(f"MCP 服务已在运行中 (PID: {pid})")
        return 0

    # 清理过期 PID
    if pid:
        _remove_pid()

    # 检查端口
    if _is_port_in_use(port, host):
        print(f"[FAIL] 端口 {port} 已被占用，请检查是否有其他服务使用该端口")
        print("  提示: 使用 --port 指定其他端口")
        return 1

    # 选择 pythonw.exe（Windows 无窗口模式）
    python_executable = sys.executable
    if sys.platform == "win32":
        pythonw = Path(python_executable).parent / "pythonw.exe"
        if pythonw.exists():
            python_executable = str(pythonw)

    cmd = [
        python_executable,
        str(_MCP_SCRIPT),
        "-t", "streamable-http",
        "--host", host,
        "-p", str(port),
    ]

    # 设置环境变量
    env = os.environ.copy()
    env["SHINEHE_HOME"] = str(_PROJECT_ROOT)

    try:
        if sys.platform == "win32":
            # Windows: 独立进程组，脱离控制台，无窗口
            flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NO_WINDOW
            )
            log_fp = open(_LOG_FILE, "a", encoding="utf-8")
            proc = subprocess.Popen(
                cmd,
                cwd=str(_PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log_fp,
                stderr=log_fp,
                creationflags=flags,
                env=env,
            )
        else:
            # Unix: 后台进程
            proc = subprocess.Popen(
                cmd,
                cwd=str(_PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )

        _write_pid(proc.pid, host, port)

        # 等待启动确认
        print(f"正在启动 MCP 服务 (端口 {port})...", end=" ", flush=True)
        for i in range(10):
            time.sleep(0.5)
            if not _is_pid_alive(proc.pid):
                _remove_pid()
                print(f"\n[FAIL] 启动失败（进程已退出），日志: {_LOG_FILE}")
                return 1
            if _is_port_in_use(port, host):
                print("[OK]")
                print(f"  PID: {proc.pid}")
                print(f"  HTTP 接入: http://{host}:{port}/mcp")
                print(f"  日志: {_LOG_FILE}")
                print("  关闭 GUI/终端不影响服务运行")
                return 0

        print(f"[OK] (PID: {proc.pid})")
        print(f"  HTTP 接入: http://{host}:{port}/mcp")
        print("  注意: 端口未响应，可能需要几秒完成初始化")
        return 0

    except Exception as e:
        print(f"[FAIL] 启动失败: {e}")
        _remove_pid()
        return 1


def do_stop() -> int:
    """停止 MCP 服务"""
    pid = _read_pid()
    if not pid:
        print("MCP 服务未运行")
        return 0

    if not _is_pid_alive(pid):
        _remove_pid()
        print("MCP 服务未运行（清理了过期 PID 文件）")
        return 0

    print(f"正在停止 MCP 服务 (PID: {pid})...", end=" ", flush=True)

    try:
        if sys.platform == "win32":
            _kill_windows(pid)
        else:
            os.kill(pid, signal.SIGTERM)

        # 等待进程退出
        for _ in range(20):
            if not _is_pid_alive(pid):
                break
            time.sleep(0.25)
        else:
            # 强制终止
            if sys.platform == "win32":
                _kill_windows(pid)
            else:
                os.kill(pid, signal.SIGKILL)

        print("[OK]" if not _is_pid_alive(pid) else "[FAIL] (强制终止)")
    except (ProcessLookupError, PermissionError) as e:
        print(f"[FAIL] ({e})")

    _remove_pid()
    return 0


def do_restart(host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> int:
    """重启 MCP 服务"""
    do_stop()
    time.sleep(1)
    return do_start(host, port)


def main():
    parser = argparse.ArgumentParser(
        prog="mcp_service",
        description="ShineHeKnowledge MCP Streamable HTTP 服务管理",
    )
    sub = parser.add_subparsers(dest="command", help="操作命令")

    # start
    p_start = sub.add_parser("start", help="启动 MCP HTTP 服务")
    p_start.add_argument("--host", default=_DEFAULT_HOST, help="监听地址")
    p_start.add_argument("--port", "-p", type=int, default=_DEFAULT_PORT, help="监听端口")

    # stop
    sub.add_parser("stop", help="停止 MCP 服务")

    # status
    sub.add_parser("status", help="查看服务状态")

    # restart
    p_restart = sub.add_parser("restart", help="重启 MCP 服务")
    p_restart.add_argument("--host", default=_DEFAULT_HOST, help="监听地址")
    p_restart.add_argument("--port", "-p", type=int, default=_DEFAULT_PORT, help="监听端口")

    args = parser.parse_args()

    if args.command == "start":
        sys.exit(do_start(args.host, args.port))
    elif args.command == "stop":
        sys.exit(do_stop())
    elif args.command == "restart":
        sys.exit(do_restart(args.host, args.port))
    elif args.command == "status":
        sys.exit(do_status())
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

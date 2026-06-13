"""Neo4j 服务进程管理 — 检测、启停本地 Neo4j 实例

用法:
    from src.services.neo4j_manager import Neo4jManager
    mgr = Neo4jManager()
    if not mgr.is_running():
        mgr.start()
        mgr.wait_for_ready(timeout=30)
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 默认 Bolt 端口
DEFAULT_BOLT_PORT = 7687

# 搜索 Neo4j 安装目录的候选路径
_SEARCH_PATHS = [
    Path("C:/neo4j"),
    Path("C:/Program Files/neo4j"),
    Path("C:/Program Files (x86)/neo4j"),
    Path(os.environ.get("NEO4J_HOME", "")) if os.environ.get("NEO4J_HOME") else None,
    Path(os.environ.get("LOCALAPPDATA", "")) / "neo4j" if os.environ.get("LOCALAPPDATA") else None,
]

# 缓存 find_neo4j_home() 结果，避免每次构造 Neo4jManager 都扫盘（涉及 C:/Program Files 等目录 iterdir）
_neo4j_home_cache: object = "__UNRESOLVED__"


def _port_is_open(host: str, port: int) -> bool:
    """检测 TCP 端口是否在监听"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def find_neo4j_home() -> Path | None:
    """自动检测 Neo4j 安装目录（结果会被缓存，环境变量变更后可调用 clear_neo4j_home_cache）

    搜索策略:
        1. 环境变量 NEO4J_HOME
        2. PATH 中的 neo4j 命令反推
        3. 常见安装路径 (C:/neo4j/neo4j-community-*, C:/Program Files/neo4j)
    """
    global _neo4j_home_cache
    if _neo4j_home_cache != "__UNRESOLVED__":
        return _neo4j_home_cache  # type: ignore[return-value]

    result: Path | None = None
    # 1. 环境变量
    env_home = os.environ.get("NEO4J_HOME")
    if env_home:
        p = Path(env_home)
        if (p / "bin" / "neo4j.bat").exists():
            result = p

    # 2. 从 PATH 中的 neo4j 命令反推
    if result is None:
        neo4j_bin = shutil.which("neo4j")
        if neo4j_bin:
            bin_dir = Path(neo4j_bin).resolve().parent
            candidate = bin_dir.parent
            if (candidate / "bin" / "neo4j.bat").exists():
                result = candidate

    # 3. 常见路径
    if result is None:
        for base in _SEARCH_PATHS:
            if base is None or not base.exists():
                continue
            if (base / "bin" / "neo4j.bat").exists():
                result = base
                break
            for child in sorted(base.iterdir(), reverse=True):
                if child.is_dir() and (child / "bin" / "neo4j.bat").exists():
                    result = child
                    break
            if result is not None:
                break

    _neo4j_home_cache = result
    return result


def clear_neo4j_home_cache():
    """清除 find_neo4j_home 缓存（用于测试或环境变量变更后强制重检）"""
    global _neo4j_home_cache
    _neo4j_home_cache = "__UNRESOLVED__"


class Neo4jManager:
    """本地 Neo4j 服务进程管理器

    支持启动、停止、状态检测和等待就绪。
    """

    def __init__(
        self,
        neo4j_home: Path | None = None,
        bolt_port: int = DEFAULT_BOLT_PORT,
        host: str = "localhost",
    ):
        self._neo4j_home = neo4j_home or find_neo4j_home()
        self._bolt_port = bolt_port
        self._host = host
        self._process: Optional[subprocess.Popen] = None

    @property
    def neo4j_home(self) -> Path | None:
        return self._neo4j_home

    @property
    def bolt_port(self) -> int:
        return self._bolt_port

    def is_installed(self) -> bool:
        """Neo4j 是否已安装（能找到 neo4j.bat）"""
        return self._neo4j_home is not None and (
            self._neo4j_home / "bin" / "neo4j.bat"
        ).exists()

    def is_running(self) -> bool:
        """检测 Bolt 端口是否在监听"""
        return _port_is_open(self._host, self._bolt_port)

    def start(self, timeout: int = 60) -> str:
        """启动 Neo4j 服务

        Args:
            timeout: 等待 Bolt 端口就绪的最大秒数

        Returns:
            操作结果描述

        Raises:
            FileNotFoundError: Neo4j 未安装
            TimeoutError: 启动超时
        """
        if self.is_running():
            return "Neo4j 已在运行"

        if not self.is_installed():
            raise FileNotFoundError(
                "未找到 Neo4j 安装目录。请设置 NEO4J_HOME 环境变量或安装 Neo4j Community Edition。"
            )

        neo4j_bat = self._neo4j_home / "bin" / "neo4j.bat"

        # 使用 CREATE_NEW_PROCESS_GROUP 在 Windows 上创建独立进程组
        # 避免子进程随父进程退出而被终止；
        # 配合 CREATE_NO_WINDOW 隐藏 neo4j.bat console 调起的 cmd 黑窗口
        # （GUI 启动 _auto_start_neo4j 时如果不隐藏会弹窗闪一下）
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )

        logger.info("Starting Neo4j: %s console", neo4j_bat)
        self._process = subprocess.Popen(
            [str(neo4j_bat), "console"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(self._neo4j_home),
            **kwargs,
        )

        # 等待 Bolt 端口就绪
        self.wait_for_ready(timeout=timeout)
        return "Neo4j 已启动"

    def stop(self, timeout: int = 15) -> str:
        """停止 Neo4j 服务

        优先通过 neo4j stop 命令优雅关闭，超时后强制终止。

        Args:
            timeout: 等待进程退出的最大秒数

        Returns:
            操作结果描述
        """
        if not self.is_running():
            return "Neo4j 未在运行"

        # 尝试优雅停止
        neo4j_bat = self._neo4j_home / "bin" / "neo4j.bat"
        if neo4j_bat.exists():
            try:
                subprocess.run(
                    [str(neo4j_bat), "stop"],
                    timeout=10,
                    capture_output=True,
                    cwd=str(self._neo4j_home),
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                logger.warning("neo4j stop failed: %s", exc)

        # 等待端口释放
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_running():
                break
            time.sleep(0.5)

        # 如果还有我们启动的进程，强制终止
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                self._process.kill()
            self._process = None

        if not self.is_running():
            return "Neo4j 已停止"
        return "Neo4j 停止请求已发送，端口可能仍在释放中"

    def wait_for_ready(self, timeout: int = 60) -> None:
        """等待 Bolt 端口就绪

        Args:
            timeout: 最大等待秒数

        Raises:
            TimeoutError: 超时未就绪
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_running():
                logger.info("Neo4j Bolt port %d is ready", self._bolt_port)
                return
            # 检查进程是否意外退出
            if self._process and self._process.poll() is not None:
                output = ""
                if self._process.stdout:
                    try:
                        output = self._process.stdout.read().decode("utf-8", errors="replace")[-500:]
                    except Exception:
                        pass
                raise RuntimeError(
                    f"Neo4j 进程意外退出 (code={self._process.returncode})\n{output}"
                )
            time.sleep(0.5)

        raise TimeoutError(
            f"Neo4j 启动超时 ({timeout}s)，Bolt 端口 {self._bolt_port} 未就绪"
        )

    def get_status(self) -> dict:
        """获取 Neo4j 状态信息

        Returns:
            {"installed": bool, "running": bool, "neo4j_home": str, "bolt_port": int}
        """
        return {
            "installed": self.is_installed(),
            "running": self.is_running(),
            "neo4j_home": str(self._neo4j_home) if self._neo4j_home else None,
            "bolt_port": self._bolt_port,
        }

"""项目初始化与客户端配置服务

提供 shinehe init 命令所需的核心功能:
- 根据 provider preset 构建初始配置
- 原子写入 YAML 配置文件
- 配置 MCP 客户端（Claude Code、Cursor、Cline 等）

从 scripts/setup_mcp.py 提取的可复用逻辑也在此集中管理。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from src.core.provider_presets import ProviderPreset, get_provider_preset

SERVER_NAME = "shinehe-kb"


class ProjectSetupService:
    """项目初始化服务"""

    # ------------------------------------------------------------------
    # 配置构建
    # ------------------------------------------------------------------

    def build_config(self, request: dict[str, Any]) -> dict[str, Any]:
        """根据请求参数构建初始配置字典

        Args:
            request: 包含以下可选键的字典:
                - local (bool): 是否本地模式（使用 Ollama）
                - provider (str): 服务商名称（默认: siliconflow）
                - path (str|None): 配置文件目标目录
                - force (bool): 是否覆盖已有配置

        Returns:
            完整的配置字典，可直接序列化为 YAML
        """
        if request.get("local"):
            return self._build_local_config()

        provider_name = request.get("provider", "siliconflow")
        preset = get_provider_preset(provider_name)
        return self._build_provider_config(preset)

    def _build_local_config(self) -> dict[str, Any]:
        """构建本地模式配置（Ollama + 离线优先）"""
        preset = get_provider_preset("ollama")
        config: dict[str, Any] = {
            "embedding": {
                "base_url": preset.embedding_base_url,
                "model": preset.embedding_model,
                "provider": preset.canonical_name,
                "reuse_llm": True,
            },
            "llm": {
                "base_url": preset.llm_base_url,
                "model": preset.llm_model,
                "provider": preset.canonical_name,
                "temperature": 0.7,
                "max_tokens": 2048,
            },
            "mcp": {
                "tool_profile": "full",
                "write_policy": "disabled",
            },
            "rag": {
                "search_mode": "blend",
                "parent_child": {"enabled": True},
                "enable_query_rewriting": True,
                "enable_rerank": False,
                "chunk_overlap": 180,
                "chunk_size": 1200,
                "score_threshold": 0.35,
                "top_k": 8,
            },
            "reranker": {
                "provider": "disabled",
                "enabled": False,
            },
            "storage": {
                "data_dir": "data",
                "db_name": "kb.db",
            },
        }
        return config

    def _build_provider_config(self, preset: ProviderPreset) -> dict[str, Any]:
        """构建基于指定服务商的配置"""
        config: dict[str, Any] = {
            "embedding": {
                "base_url": preset.embedding_base_url,
                "model": preset.embedding_model,
                "provider": preset.canonical_name,
                "reuse_llm": True,
            },
            "llm": {
                "base_url": preset.llm_base_url,
                "model": preset.llm_model,
                "provider": preset.canonical_name,
                "temperature": 0.7,
                "max_tokens": 2048,
            },
            "rag": {
                "chunk_overlap": 180,
                "chunk_size": 1200,
                "score_threshold": 0.35,
                "top_k": 8,
                "search_mode": "blend",
            },
            "storage": {
                "data_dir": "data",
                "db_name": "kb.db",
            },
        }

        if preset.reranker_base_url:
            config["reranker"] = {
                "base_url": preset.reranker_base_url,
                "model": preset.reranker_model,
                "enabled": True,
                "provider": preset.canonical_name,
            }

        return config

    # ------------------------------------------------------------------
    # 配置文件写入
    # ------------------------------------------------------------------

    def write_config(
        self,
        target: Path | None,
        config: dict[str, Any],
        force: bool = False,
    ) -> Path:
        """原子写入 YAML 配置文件

        Args:
            target: 目标目录。None 时使用 ~/.shinehe/ 或 SHINEHE_HOME
            config: 配置字典
            force: 是否覆盖已有文件

        Returns:
            写入的配置文件路径

        Raises:
            FileExistsError: 文件已存在且 force=False
        """
        if target is None:
            config_dir = self._get_config_dir()
        else:
            config_dir = Path(target)

        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "config.yaml"

        if config_path.exists() and not force:
            raise FileExistsError(
                f"配置文件已存在: {config_path}（使用 --force 覆盖）"
            )

        # 原子写入: 先写临时文件再重命名
        fd, tmp_path = tempfile.mkstemp(
            dir=str(config_dir), suffix=".yaml.tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
            os.replace(tmp_path, str(config_path))
        except Exception:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        return config_path

    @staticmethod
    def _get_config_dir() -> Path:
        """获取配置目录路径"""
        env_home = os.environ.get("SHINEHE_HOME")
        if env_home:
            return Path(env_home)
        return Path.home() / ".shinehe"

    # ------------------------------------------------------------------
    # MCP 客户端配置（从 scripts/setup_mcp.py 提取）
    # ------------------------------------------------------------------

    def build_server_config(self, config_path: Path | None = None) -> dict[str, Any]:
        """构建 MCP Server 的 stdio 配置字典

        优先使用已安装的 shinehe-mcp 命令，回退到 Python 直接执行。
        """
        project_root = self._detect_project_root()
        shinehe_cmd = shutil.which("shinehe-mcp")

        if shinehe_cmd:
            return {
                "command": "shinehe-mcp",
                "args": [],
                "cwd": str(project_root),
                "env": {"SHINEHE_HOME": str(project_root)},
                "type": "stdio",
            }

        return {
            "command": sys.executable,
            "args": [str(project_root / "run_mcp.py")],
            "cwd": str(project_root),
            "env": {"SHINEHE_HOME": str(project_root)},
            "type": "stdio",
        }

    def configure_clients(
        self,
        clients: list[str],
        server_config: dict[str, Any],
    ) -> None:
        """为指定的 MCP 客户端写入配置

        Args:
            clients: 客户端名称列表（如 ["claude-code", "cursor"]）
            server_config: MCP Server 配置字典
        """
        agent_paths = get_agent_config_paths()
        for client_name in clients:
            if client_name not in agent_paths:
                print(f"[WARN] 未知客户端: {client_name}，跳过")
                continue
            add_to_agent_config(client_name, agent_paths[client_name], server_config)

    @staticmethod
    def _detect_project_root() -> Path:
        """检测项目根目录"""
        # 优先使用 SHINEHE_HOME
        env_root = os.environ.get("SHINEHE_HOME")
        if env_root:
            return Path(env_root)
        # 回退到当前工作目录的父级（src/ -> project_root）
        return Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# 独立可复用函数（供 scripts/setup_mcp.py 等外部脚本调用）
# ---------------------------------------------------------------------------


def get_agent_config_paths() -> dict[str, Path]:
    """返回各 MCP 客户端的配置文件路径（按平台区分）"""
    home = Path.home()
    if platform.system() == "Windows":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return {
            "claude-code": home / ".claude.json",
            "cursor": home / ".cursor" / "mcp.json",
            "cline": appdata
            / "Code"
            / "User"
            / "globalStorage"
            / "saoudrizwan.claude-dev"
            / "settings"
            / "cline_mcp_settings.json",
            "windsurf": appdata / "WindSurf" / "mcp_settings.json",
            "roo-code": appdata
            / "Code"
            / "User"
            / "globalStorage"
            / "rooveterinaryinc.roo-cline"
            / "settings"
            / "cline_mcp_settings.json",
            "opencode": home / ".config" / "opencode" / "opencode.json",
        }

    support = home / "Library" / "Application Support"
    return {
        "claude-code": home / ".claude.json",
        "cursor": home / ".cursor" / "mcp.json",
        "cline": support
        / "Code"
        / "User"
        / "globalStorage"
        / "saoudrizwan.claude-dev"
        / "settings"
        / "cline_mcp_settings.json",
        "windsurf": home / ".codeium" / "windsurf" / "mcp_config.json",
        "roo-code": support
        / "Code"
        / "User"
        / "globalStorage"
        / "rooveterinaryinc.roo-cline"
        / "settings"
        / "cline_mcp_settings.json",
        "opencode": home / ".config" / "opencode" / "opencode.json",
    }


def build_server_config() -> dict[str, Any]:
    """构建 MCP Server 配置（独立函数，供 scripts/setup_mcp.py 使用）"""
    service = ProjectSetupService()
    return service.build_server_config()


def add_to_agent_config(
    agent_name: str,
    config_path: Path,
    server_config: dict[str, Any],
) -> None:
    """将 MCP Server 配置写入指定客户端的配置文件

    Args:
        agent_name: 客户端名称（如 "claude-code", "cursor", "opencode"）
        config_path: 客户端配置文件路径
        server_config: MCP Server 配置字典
    """
    config = _read_json(config_path)

    if agent_name == "opencode":
        config.setdefault("mcp", {})
        config["mcp"][SERVER_NAME] = {
            "type": "local",
            "command": [
                server_config["command"],
                *server_config.get("args", []),
            ],
            "environment": server_config.get("env", {}),
            "enabled": True,
        }
    else:
        config.setdefault("mcpServers", {})
        config["mcpServers"][SERVER_NAME] = server_config

    _write_json(config_path, config)
    print(f"[OK] {agent_name}: {config_path}")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return dict(json.load(f))


def _write_json(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")

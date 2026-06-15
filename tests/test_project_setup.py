"""ProjectSetupService 单元测试

测试配置构建、YAML 写入、客户端配置，不依赖 PySide6。
"""
import json
from unittest.mock import patch

import pytest
import yaml

from src.services.project_setup import (
    SERVER_NAME,
    ProjectSetupService,
    add_to_agent_config,
    get_agent_config_paths,
)

# ---------------------------------------------------------------------------
# build_config 测试
# ---------------------------------------------------------------------------


class TestBuildConfig:
    """测试配置构建逻辑"""

    def test_local_config_uses_ollama(self):
        """本地模式使用 Ollama 预设"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})

        assert config["embedding"]["base_url"] == "http://localhost:11434/v1"
        assert config["embedding"]["model"] == "nomic-embed-text"
        assert config["llm"]["base_url"] == "http://localhost:11434/v1"
        assert config["llm"]["model"] == "qwen2.5"

    def test_local_config_mcp_settings(self):
        """本地模式 MCP 配置正确"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})

        assert config["mcp"]["tool_profile"] == "full"
        assert config["mcp"]["write_policy"] == "disabled"

    def test_local_config_rag_settings(self):
        """本地模式 RAG 配置正确"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})

        assert config["rag"]["search_mode"] == "blend"
        assert config["rag"]["parent_child"]["enabled"] is True
        assert config["rag"]["enable_query_rewriting"] is True
        assert config["rag"]["enable_rerank"] is False

    def test_local_config_reranker_disabled(self):
        """本地模式 Reranker 禁用"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})

        assert config["reranker"]["provider"] == "disabled"
        assert config["reranker"]["enabled"] is False

    def test_provider_config_siliconflow(self):
        """SiliconFlow 预设配置正确"""
        service = ProjectSetupService()
        config = service.build_config({"provider": "siliconflow"})

        assert config["embedding"]["base_url"] == "https://api.siliconflow.cn/v1"
        assert config["embedding"]["model"] == "BAAI/bge-m3"
        assert config["llm"]["model"] == "Qwen/Qwen3-8B"
        assert config["reranker"]["base_url"] == "https://api.siliconflow.cn/v1"
        assert config["reranker"]["model"] == "BAAI/bge-reranker-v2-m3"

    def test_provider_config_openai(self):
        """OpenAI 预设配置正确"""
        service = ProjectSetupService()
        config = service.build_config({"provider": "openai"})

        assert config["embedding"]["base_url"] == "https://api.openai.com/v1"
        assert config["embedding"]["model"] == "text-embedding-3-small"
        assert config["llm"]["model"] == "gpt-4o-mini"
        assert "reranker" not in config

    def test_provider_config_deepseek(self):
        """DeepSeek 预设无 reranker"""
        service = ProjectSetupService()
        config = service.build_config({"provider": "deepseek"})

        assert config["llm"]["model"] == "deepseek-chat"
        assert "reranker" not in config

    def test_default_provider_is_siliconflow(self):
        """默认使用 siliconflow 服务商"""
        service = ProjectSetupService()
        config = service.build_config({})
        assert config["llm"]["provider"] == "siliconflow"


# ---------------------------------------------------------------------------
# write_config 测试
# ---------------------------------------------------------------------------


class TestWriteConfig:
    """测试配置文件写入"""

    def test_write_config_creates_file(self, tmp_path):
        """写入配置文件"""
        service = ProjectSetupService()
        config = {"llm": {"model": "test"}}
        path = service.write_config(tmp_path, config)

        assert path.exists()
        assert path.name == "config.yaml"

    def test_write_config_content(self, tmp_path):
        """写入的配置内容正确"""
        service = ProjectSetupService()
        config = {"llm": {"model": "test-model", "base_url": "http://test"}}
        path = service.write_config(tmp_path, config)

        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)

        assert loaded["llm"]["model"] == "test-model"
        assert loaded["llm"]["base_url"] == "http://test"

    def test_write_config_no_overwrite_without_force(self, tmp_path):
        """无 --force 时不覆盖已有文件"""
        service = ProjectSetupService()
        config = {"llm": {"model": "test"}}

        # 先创建一次
        service.write_config(tmp_path, config)

        # 再次写入应抛出 FileExistsError
        with pytest.raises(FileExistsError):
            service.write_config(tmp_path, config)

    def test_write_config_force_overwrite(self, tmp_path):
        """--force 可覆盖已有文件"""
        service = ProjectSetupService()

        # 先创建一次
        service.write_config(tmp_path, {"version": 1})

        # force=True 覆盖
        path = service.write_config(tmp_path, {"version": 2}, force=True)
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        assert loaded["version"] == 2

    def test_write_config_creates_directories(self, tmp_path):
        """自动创建不存在的目录"""
        service = ProjectSetupService()
        target = tmp_path / "sub" / "dir"
        config = {"test": True}

        path = service.write_config(target, config)
        assert path.exists()

    def test_write_config_default_dir(self, tmp_path, monkeypatch):
        """target=None 时使用 SHINEHE_HOME"""
        monkeypatch.setenv("SHINEHE_HOME", str(tmp_path / "shinehe"))
        service = ProjectSetupService()
        config = {"test": True}

        path = service.write_config(None, config)
        assert "shinehe" in str(path)
        assert path.exists()


# ---------------------------------------------------------------------------
# configure_clients 测试
# ---------------------------------------------------------------------------


class TestConfigureClients:
    """测试 MCP 客户端配置"""

    def test_configure_known_client(self, tmp_path):
        """配置已知客户端"""
        config_path = tmp_path / "mcp.json"
        server_config = {
            "command": "shinehe-mcp",
            "args": [],
            "type": "stdio",
        }

        add_to_agent_config("cursor", config_path, server_config)

        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert SERVER_NAME in data["mcpServers"]
        assert data["mcpServers"][SERVER_NAME]["command"] == "shinehe-mcp"

    def test_configure_opencode_client(self, tmp_path):
        """opencode 使用特殊格式"""
        config_path = tmp_path / "opencode.json"
        server_config = {
            "command": "shinehe-mcp",
            "args": ["--transport", "stdio"],
            "env": {"SHINEHE_HOME": "/tmp"},
            "type": "stdio",
        }

        add_to_agent_config("opencode", config_path, server_config)

        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert SERVER_NAME in data["mcp"]
        assert data["mcp"][SERVER_NAME]["type"] == "local"
        assert data["mcp"][SERVER_NAME]["enabled"] is True

    def test_configure_preserves_existing(self, tmp_path):
        """配置时保留已有的其他服务器配置"""
        config_path = tmp_path / "mcp.json"

        # 先写入已有配置
        existing = {"mcpServers": {"other-server": {"command": "other"}}}
        config_path.write_text(json.dumps(existing), encoding="utf-8")

        server_config = {"command": "shinehe-mcp", "args": [], "type": "stdio"}
        add_to_agent_config("cursor", config_path, server_config)

        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "other-server" in data["mcpServers"]
        assert SERVER_NAME in data["mcpServers"]

    def test_configure_creates_parent_dirs(self, tmp_path):
        """自动创建不存在的父目录"""
        config_path = tmp_path / "deep" / "nested" / "mcp.json"
        server_config = {"command": "shinehe-mcp", "args": [], "type": "stdio"}

        add_to_agent_config("cursor", config_path, server_config)
        assert config_path.exists()

    def test_get_agent_config_paths_returns_dict(self):
        """get_agent_config_paths 返回非空字典"""
        paths = get_agent_config_paths()
        assert isinstance(paths, dict)
        assert len(paths) >= 4
        assert "claude-code" in paths
        assert "cursor" in paths
        assert "cline" in paths

    def test_configure_unknown_client_warns(self, tmp_path, capsys):
        """configure_clients 对未知客户端发出警告"""
        service = ProjectSetupService()
        server_config = {"command": "test", "args": [], "type": "stdio"}

        with patch(
            "src.services.project_setup.get_agent_config_paths",
            return_value={"cursor": tmp_path / "mcp.json"},
        ):
            service.configure_clients(["unknown-client"], server_config)

        captured = capsys.readouterr()
        assert "WARN" in captured.out or "unknown" in captured.out.lower()

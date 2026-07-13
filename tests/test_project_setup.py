"""ProjectSetupService 单元测试

测试配置构建、YAML 写入、客户端配置，不依赖 PySide6。
"""
import json
from pathlib import Path
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
        """本地 verified 默认：core + 写关闭"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})

        assert config["mcp"]["tool_profile"] == "core"
        assert config["mcp"]["write_policy"] == "disabled"
        assert config["mcp"]["experimental_tools_enabled"] is False

    def test_local_config_rag_settings(self):
        """本地 verified RAG：hybrid_verified 意图 + parent_child"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})

        assert config["rag"]["search_mode"] == "hybrid_verified"
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

    def test_local_config_has_knowledge_workflow(self):
        """local 模式生成 knowledge_workflow 段,默认 verified"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})
        kw = config["knowledge_workflow"]
        assert kw["mode"] == "verified"
        assert kw["raw_dir"] == "raw"
        assert kw["wiki_dir"] == "wiki"
        assert kw["schema_file"] == "schema/AGENTS.md"
        assert kw["maintain_index_md"] is True
        assert kw["maintain_log_md"] is True

    def test_local_config_wiki_safe_defaults(self):
        """verified: 可读、不可 authoring、不自动编译/发布"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})
        assert config["wiki"]["auto_publish"] is False
        assert config["wiki"]["lint_contradictions"] is True
        assert config["wiki"]["enabled"] is True
        assert config["wiki"]["read_enabled"] is True
        assert config["wiki"]["authoring_enabled"] is False
        assert config["wiki"]["auto_compile"] is False

    def test_local_config_mcp_does_not_expose_wiki_tools_by_default(self):
        """verified local: experimental_tools 关闭"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})
        assert config["mcp"]["experimental_tools_enabled"] is False
        assert config["mcp"]["allow_http_write"] is False

    def test_provider_config_has_knowledge_workflow(self):
        """provider 默认同样 verified"""
        service = ProjectSetupService()
        config = service.build_config({"provider": "siliconflow"})
        assert config["knowledge_workflow"]["mode"] == "verified"
        assert config["wiki"]["auto_publish"] is False
        assert config["wiki"]["authoring_enabled"] is False

    def test_provider_config_mcp_safe_defaults(self):
        """verified provider: core + write disabled"""
        service = ProjectSetupService()
        config = service.build_config({"provider": "siliconflow"})
        assert config["mcp"]["tool_profile"] == "core"
        assert config["mcp"]["experimental_tools_enabled"] is False
        assert config["mcp"]["write_policy"] == "disabled"
        assert config["mcp"]["allow_http_write"] is False

    def test_authoring_mode_local(self):
        """--mode authoring 恢复完整维护面"""
        service = ProjectSetupService()
        config = service.build_config({"local": True, "mode": "authoring"})
        assert config["knowledge_workflow"]["mode"] == "authoring"
        assert config["wiki"]["authoring_enabled"] is True
        assert config["wiki"]["auto_compile"] is True
        assert config["wiki"]["auto_publish"] is False
        assert config["mcp"]["tool_profile"] == "extended"
        assert config["mcp"]["experimental_tools_enabled"] is True
        assert config["mcp"]["write_policy"] == "disabled"
        assert config["rag"]["size_aware"]["enabled"] is True

    def test_authoring_mode_provider_write_policy(self):
        service = ProjectSetupService()
        config = service.build_config({"provider": "siliconflow", "mode": "authoring"})
        assert config["mcp"]["write_policy"] == "local_confirm"

    def test_evidence_only_mode(self):
        service = ProjectSetupService()
        config = service.build_config({"local": True, "mode": "evidence-only"})
        assert config["knowledge_workflow"]["mode"] == "evidence_only"
        assert config["wiki"]["read_enabled"] is False
        assert config["wiki"]["authoring_enabled"] is False
        assert config["rag"]["verified_knowledge"]["enabled"] is False

    def test_wiki_first_alias_maps_to_authoring_config(self):
        """旧 CLI/请求 wiki_first 解析为 authoring 配置"""
        service = ProjectSetupService()
        config = service.build_config({"local": True, "mode": "wiki_first"})
        assert config["knowledge_workflow"]["mode"] == "authoring"
        assert config["wiki"]["authoring_enabled"] is True

    def test_wiki_first_defaults_helper_structure(self):
        """_wiki_first_defaults 兼容别名 → authoring 两段"""
        service = ProjectSetupService()
        defaults = service._wiki_first_defaults()
        assert set(defaults.keys()) == {"knowledge_workflow", "wiki"}
        assert defaults["knowledge_workflow"]["mode"] == "authoring"


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
# write_wiki_first_layout 测试
# ---------------------------------------------------------------------------


class TestWikiFirstLayout:
    """测试 wiki-first 目录契约生成"""

    def test_creates_all_directories(self, tmp_path):
        """创建全部 8 个目录"""
        from src.services.project_setup import WIKI_FIRST_DIRS

        service = ProjectSetupService()
        created = service.write_wiki_first_layout(tmp_path)

        rel = {p.relative_to(tmp_path).as_posix() for p in created}
        assert rel == set(WIKI_FIRST_DIRS)
        for rel_dir in WIKI_FIRST_DIRS:
            assert (tmp_path / rel_dir).is_dir()

    def test_creates_agents_md(self, tmp_path):
        """生成 schema/AGENTS.md 模板"""
        service = ProjectSetupService()
        service.write_wiki_first_layout(tmp_path)

        agents_md = tmp_path / "schema" / "AGENTS.md"
        assert agents_md.exists()
        content = agents_md.read_text(encoding="utf-8")
        assert "Source of truth" in content
        assert "raw/" in content
        assert "Page types" in content
        assert "Ingest workflow" in content

    def test_idempotent(self, tmp_path):
        """重复调用不抛错"""
        service = ProjectSetupService()
        service.write_wiki_first_layout(tmp_path)
        service.write_wiki_first_layout(tmp_path)  # 不抛异常
        assert (tmp_path / "schema" / "AGENTS.md").exists()

    def test_preserves_custom_agents_md(self, tmp_path):
        """已存在的 AGENTS.md 不被覆盖"""
        service = ProjectSetupService()
        (tmp_path / "schema").mkdir(parents=True)
        custom = "# My Custom AGENTS Rules\n"
        (tmp_path / "schema" / "AGENTS.md").write_text(custom, encoding="utf-8")

        service.write_wiki_first_layout(tmp_path)

        assert (tmp_path / "schema" / "AGENTS.md").read_text(encoding="utf-8") == custom

    def test_returns_created_dir_list(self, tmp_path):
        """返回值是创建的目录路径列表"""
        service = ProjectSetupService()
        created = service.write_wiki_first_layout(tmp_path)
        assert len(created) == 8
        assert all(p.is_dir() for p in created)


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


# ---------------------------------------------------------------------------
# config.example.yaml 一致性测试
# ---------------------------------------------------------------------------


class TestConfigExampleConvergence:
    """config.example.yaml 与 build_config 默认值一致性"""

    @pytest.fixture(scope="class")
    def example_config(self):
        project_root = Path(__file__).resolve().parent.parent
        with open(project_root / "config.example.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_no_chroma_dir(self, example_config):
        """legacy chroma_dir 已清理"""
        assert "chroma_dir" not in example_config.get("storage", {})

    def test_wiki_safe_defaults(self, example_config):
        """wiki 安全默认:auto_publish=False, lint_contradictions=True"""
        wiki = example_config["wiki"]
        assert wiki["auto_publish"] is False
        assert wiki["lint_contradictions"] is True
        assert wiki.get("authoring_enabled") is False
        assert wiki.get("read_enabled") is True

    def test_example_default_verified_mode(self, example_config):
        """config.example 默认 verified，MCP 不开启写与 experimental"""
        assert example_config["knowledge_workflow"]["mode"] == "verified"
        mcp = example_config["mcp"]
        assert mcp["experimental_tools_enabled"] is False
        assert mcp["write_policy"] == "disabled"
        assert mcp["tool_profile"] == "core"

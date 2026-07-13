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
from src.utils.knowledge_mode import (
    MODE_AUTHORING,
    MODE_EVIDENCE_ONLY,
    MODE_VERIFIED,
    resolve_knowledge_mode,
)

SERVER_NAME = "shinehe-kb"

# wiki-first 目录契约:相对项目根的 8 个目录
WIKI_FIRST_DIRS: tuple[str, ...] = (
    "raw",
    "wiki/sources",
    "wiki/entities",
    "wiki/concepts",
    "wiki/comparisons",
    "wiki/syntheses",
    "schema",
    "artifacts/eval",
)

AGENTS_MD_TEMPLATE = """\
# AGENTS.md

> ShineHeKnowledge wiki-first 知识维护规约。由 `shinehe init` 生成,可自由定制。

## Source of truth
- `raw/` 下所有文件只读,agent 不得直接修改
- 所有综合结论必须可追溯到 `raw/` 文件或已有 wiki 页

## Page types
- `wiki/sources/*.md`     单源摘要页(规则模板生成)
- `wiki/entities/*.md`    实体页(LLM 维护)
- `wiki/concepts/*.md`    概念页(LLM 维护)
- `wiki/comparisons/*.md` 对比页(query 回写)
- `wiki/syntheses/*.md`   综合页(query 回写)

## Ingest workflow
- 读取 `raw/` 新源
- 生成 source summary(`wiki/sources/`)
- 识别并更新相关 entities/concepts
- 更新 `wiki/index.md`,追加 `wiki/log.md`
- 与旧结论冲突时显式标注

## Query workflow
- 先读 `wiki/index.md` 定位相关页
- 再读相关 wiki 页
- 证据不足时回到 `raw/` 检索
- 高价值回答可保存为新 wiki 页(`comparisons/syntheses`,draft 状态)

## Lint workflow
- 孤儿页、矛盾、过时 claim、缺失 backlinks 四类检查
- 发现问题标注待修,不自动删除
"""


class ProjectSetupService:
    """项目初始化服务"""

    # ------------------------------------------------------------------
    # 配置构建
    # ------------------------------------------------------------------

    @staticmethod
    def _workflow_paths() -> dict[str, Any]:
        """Shared knowledge_workflow path keys (all modes that keep wiki dirs)."""
        return {
            "raw_dir": "raw",
            "wiki_dir": "wiki",
            "schema_file": "schema/AGENTS.md",
            "source_summary_dir": "wiki/sources",
            "entity_dir": "wiki/entities",
            "concept_dir": "wiki/concepts",
            "synthesis_dir": "wiki/syntheses",
            "comparison_dir": "wiki/comparisons",
            "maintain_index_md": True,
            "maintain_log_md": True,
        }

    @staticmethod
    def _verified_mode_defaults() -> dict[str, Any]:
        """Default product mode: Raw + verified Wiki read intent, no authoring."""
        return {
            "knowledge_workflow": {
                "mode": MODE_VERIFIED,
                **ProjectSetupService._workflow_paths(),
            },
            "wiki": {
                "enabled": True,
                "read_enabled": True,
                "authoring_enabled": False,
                "auto_compile": False,
                "auto_link": True,
                "auto_publish": False,
                "lint_contradictions": True,
                "max_llm_calls_per_ingest": 3,
                "query_save_min_length": 100,
                "serving": {
                    "enabled": True,
                    "allowed_claim_statuses": ["active"],
                    "require_block_evidence": True,
                    "exclude_stale": True,
                    "exclude_unsupported": True,
                    "exclude_retracted": True,
                    "require_validation_passed": True,
                    "require_review_approved": True,
                    "require_published_revision": True,
                    "unresolved_policy": "disclose",
                    "contradiction_policy": "disclose",
                    "on_failure": "raw_fallback",
                    "max_claims_per_query": 8,
                    "max_evidence_per_claim": 3,
                },
            },
        }

    @staticmethod
    def _authoring_mode_defaults() -> dict[str, Any]:
        """Authoring mode: full Wiki maintenance (replaces wiki_first defaults)."""
        return {
            "knowledge_workflow": {
                "mode": MODE_AUTHORING,
                **ProjectSetupService._workflow_paths(),
            },
            "wiki": {
                "enabled": True,
                "read_enabled": True,
                "authoring_enabled": True,
                "auto_compile": True,
                "auto_link": True,
                "auto_publish": False,  # review gate — never auto-publish
                "lint_contradictions": True,
                "max_llm_calls_per_ingest": 3,
                "query_save_min_length": 100,
                "serving": {
                    "enabled": True,
                    "allowed_claim_statuses": ["active"],
                    "require_block_evidence": True,
                    "exclude_stale": True,
                    "exclude_unsupported": True,
                    "exclude_retracted": True,
                    "require_validation_passed": True,
                    "require_review_approved": True,
                    "require_published_revision": True,
                    "unresolved_policy": "disclose",
                    "contradiction_policy": "disclose",
                    "on_failure": "raw_fallback",
                    "max_claims_per_query": 8,
                    "max_evidence_per_claim": 3,
                },
            },
        }

    @staticmethod
    def _evidence_only_mode_defaults() -> dict[str, Any]:
        """Evidence-only: Raw Retrieval, Wiki read/authoring off."""
        return {
            "knowledge_workflow": {
                "mode": MODE_EVIDENCE_ONLY,
                "raw_dir": "raw",
                "wiki_dir": "wiki",
                "schema_file": "schema/AGENTS.md",
                "maintain_index_md": False,
                "maintain_log_md": False,
            },
            "wiki": {
                "enabled": False,
                "read_enabled": False,
                "authoring_enabled": False,
                "auto_compile": False,
                "auto_link": False,
                "auto_publish": False,
                "lint_contradictions": False,
                "serving": {
                    "enabled": False,
                    "on_failure": "raw_fallback",
                },
            },
        }

    @staticmethod
    def _wiki_first_defaults() -> dict[str, Any]:
        """Back-compat alias for authoring defaults (historical name).

        Tests and callers that still invoke ``_wiki_first_defaults`` receive
        the authoring-mode payload (``mode: authoring``). New code should call
        ``_authoring_mode_defaults`` or ``_mode_defaults(mode)``.
        """
        return ProjectSetupService._authoring_mode_defaults()

    @staticmethod
    def _mode_defaults(mode: str) -> dict[str, Any]:
        """Return knowledge_workflow + wiki defaults for a resolved mode."""
        resolved = resolve_knowledge_mode(mode)
        if resolved == MODE_AUTHORING:
            return ProjectSetupService._authoring_mode_defaults()
        if resolved == MODE_EVIDENCE_ONLY:
            return ProjectSetupService._evidence_only_mode_defaults()
        return ProjectSetupService._verified_mode_defaults()

    @staticmethod
    def _size_aware_defaults() -> dict[str, Any]:
        """第二阶段规模自适应路由默认段(wiki_first 项目 enabled=true)。

        legacy 项目缺省不注入(走 ``Config.get`` 默认 ``enabled=false``);由
        ``_build_local_config`` / ``_build_provider_config`` 合入各自 rag 段。
        不能放进 ``_wiki_first_defaults``,因其经 ``config.update`` 浅合并,
        会整体覆盖 build 函数已设的 ``rag`` 段。
        """
        return {
            "enabled": True,
            "small_query_max_tokens": 12,
            "small_wiki_page_threshold": 3,
            "intent_words_large": ["哪些", "所有", "对比", "全部", "列举"],
            "llm_fallback": False,
        }

    @staticmethod
    def _wiki_parent_defaults() -> dict[str, Any]:
        """第二阶段 W2 wiki parent-child 默认段(wiki_first 项目 enabled=true)。

        wiki 命中 entity/concept/synthesis/comparison 页时,用 knowledge_id 回查
        source 页摘要写入候选 parent_content(与 block parent-child 对称)。
        legacy 项目缺省不注入;由 ``_build_local_config`` /
        ``_build_provider_config`` 合入各自 rag 段(同 ``_size_aware_defaults``,
        不能放进 ``_wiki_first_defaults`` 浅合并坑)。
        """
        return {
            "enabled": True,
            "max_parent_chars": 2000,
        }

    @staticmethod
    def _lexical_zh_defaults() -> dict[str, Any]:
        """第二阶段 W3 中文 lexical 强化默认段(专名词典 + 同义词扩展)。

        语种权重 rrf_weight_keyword_zh/en 在 rag 段顶层(见 _build_local_config
        /_build_provider_config),与 hybrid_search.py 读取位置一致;不放本段
        (避免嵌套在 lexical_zh 子段读不到)。legacy 缺省不注入(enabled 走
        Config.get 默认 false);由 _build_local_config / _build_provider_config
        合入各自 rag 段(同 _size_aware/_wiki_parent 浅合并坑)。
        """
        return {
            "enabled": True,
            "dict_path": "data/lexical_zh_dict.txt",
            "synonym_path": "data/lexical_zh_synonyms.txt",
        }

    def build_config(self, request: dict[str, Any]) -> dict[str, Any]:
        """根据请求参数构建初始配置字典

        Args:
            request: 包含以下可选键的字典:
                - local (bool): 是否本地模式（使用 Ollama）
                - provider (str): 服务商名称（默认: siliconflow）
                - path (str|None): 配置文件目标目录
                - force (bool): 是否覆盖已有配置
                - mode (str): verified | authoring | evidence_only
                  （兼容 wiki_first / legacy / evidence-only）

        Returns:
            完整的配置字典，可直接序列化为 YAML
        """
        mode = resolve_knowledge_mode(request.get("mode") or MODE_VERIFIED)
        if request.get("local"):
            return self._build_local_config(mode)

        provider_name = request.get("provider", "siliconflow")
        preset = get_provider_preset(provider_name)
        return self._build_provider_config(preset, mode)

    def _mcp_defaults_for_mode(
        self, mode: str, *, local: bool,
    ) -> dict[str, Any]:
        """MCP surface by knowledge mode (Spec §5.2–5.4)."""
        resolved = resolve_knowledge_mode(mode)
        if resolved == MODE_AUTHORING:
            return {
                "tool_profile": "extended",
                "write_policy": "local_confirm",
                "experimental_tools_enabled": True,
                "allow_http_write": False,
                "enable_legacy_aliases": False,
            }
        if resolved == MODE_EVIDENCE_ONLY:
            return {
                "tool_profile": "core",
                "write_policy": "disabled",
                "experimental_tools_enabled": False,
                "allow_http_write": False,
                "enable_legacy_aliases": False,
            }
        # verified (default)
        return {
            "tool_profile": "core",
            "write_policy": "disabled",
            "experimental_tools_enabled": False,
            "allow_http_write": False,
            "enable_legacy_aliases": False,
        }

    def _rag_defaults_for_mode(self, mode: str) -> dict[str, Any]:
        """RAG defaults; authoring keeps wiki size-aware helpers."""
        resolved = resolve_knowledge_mode(mode)
        rag: dict[str, Any] = {
            "search_mode": "blend",
            "parent_child": {"enabled": True},
            "enable_query_rewriting": True,
            "chunk_overlap": 180,
            "chunk_size": 1200,
            "score_threshold": 0.35,
            "top_k": 8,
            "rrf_weight_keyword_zh": 0.7,
            "rrf_weight_keyword_en": 0.5,
            "verified_knowledge": {
                "enabled": resolved in {MODE_VERIFIED, MODE_AUTHORING},
                "raw_candidate_multiplier": 3,
                "wiki_candidate_multiplier": 2,
                "raw_weight": 0.60,
                "wiki_weight": 0.40,
                "evidence_alignment_enabled": True,
                "stale_fallback_to_raw": True,
                "empty_wiki_fallback_to_raw": True,
            },
        }
        if resolved == MODE_AUTHORING:
            rag["size_aware"] = self._size_aware_defaults()
            rag["wiki_parent_child"] = self._wiki_parent_defaults()
            rag["lexical_zh"] = self._lexical_zh_defaults()
            rag["wiki_read"] = {"sqlite_fallback": True}
        elif resolved == MODE_VERIFIED:
            # Read intent on; fusion stages land in Phase 3 — keep helpers off.
            rag["size_aware"] = {**self._size_aware_defaults(), "enabled": False}
            rag["wiki_parent_child"] = {
                **self._wiki_parent_defaults(), "enabled": False,
            }
            rag["lexical_zh"] = {
                **self._lexical_zh_defaults(), "enabled": False,
            }
            rag["wiki_read"] = {"sqlite_fallback": True}
            rag["search_mode"] = "hybrid_verified"
        else:
            rag["verified_knowledge"] = {"enabled": False}
        return rag

    def _build_local_config(self, mode: str = MODE_VERIFIED) -> dict[str, Any]:
        """构建本地模式配置（Ollama + 离线优先）"""
        resolved = resolve_knowledge_mode(mode)
        preset = get_provider_preset("ollama")
        rag = self._rag_defaults_for_mode(resolved)
        rag["enable_rerank"] = False
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
            "mcp": self._mcp_defaults_for_mode(resolved, local=True),
            "rag": rag,
            "reranker": {
                "provider": "disabled",
                "enabled": False,
            },
            "storage": {
                "data_dir": "data",
                "db_name": "kb.db",
            },
        }
        config.update(self._mode_defaults(resolved))
        return config

    def _build_provider_config(
        self,
        preset: ProviderPreset,
        mode: str = MODE_VERIFIED,
    ) -> dict[str, Any]:
        """构建基于指定服务商的配置"""
        resolved = resolve_knowledge_mode(mode)
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
            "mcp": self._mcp_defaults_for_mode(resolved, local=False),
            "rag": self._rag_defaults_for_mode(resolved),
            "storage": {
                "data_dir": "data",
                "db_name": "kb.db",
            },
        }

        if preset.reranker_base_url and resolved != MODE_EVIDENCE_ONLY:
            config["reranker"] = {
                "base_url": preset.reranker_base_url,
                "model": preset.reranker_model,
                "enabled": True,
                "provider": preset.canonical_name,
            }

        config.update(self._mode_defaults(resolved))
        return config

    # ------------------------------------------------------------------
    # 配置文件写入
    # ------------------------------------------------------------------

    def write_wiki_first_layout(self, base_dir: Path) -> list[Path]:
        """在 base_dir 下创建 wiki-first 目录契约 + schema/AGENTS.md。

        创建 raw/、wiki/{sources,entities,concepts,comparisons,syntheses}/、
        schema/、artifacts/eval/ 共 8 个目录,并写入 schema/AGENTS.md 模板。
        幂等:已存在的目录保留;已存在的 AGENTS.md 不覆盖(尊重用户定制)。

        Args:
            base_dir: 项目根目录

        Returns:
            创建(或已存在)的目录路径列表
        """
        base = Path(base_dir)
        created: list[Path] = []
        for rel in WIKI_FIRST_DIRS:
            d = base / rel
            d.mkdir(parents=True, exist_ok=True)
            created.append(d)

        agents_md = base / "schema" / "AGENTS.md"
        if not agents_md.exists():
            agents_md.write_text(AGENTS_MD_TEMPLATE, encoding="utf-8")

        # W3: 中文 lexical 强化空模板（幂等，不覆盖用户已填内容）
        data_dir = base_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        dict_tpl = data_dir / "lexical_zh_dict.txt"
        if not dict_tpl.exists():
            dict_tpl.write_text(
                "# jieba 自定义专名词典（每行: 词 [词频] [词性]，如 'FTTR 1000 nz'）\n"
                "# 留空则不加载。shinehe init 生成此空模板。\n",
                encoding="utf-8",
            )
        syn_tpl = data_dir / "lexical_zh_synonyms.txt"
        if not syn_tpl.exists():
            syn_tpl.write_text(
                "# 同义词词典（每行: 词 同义词1 同义词2，如 '知识库 知识仓库 KB'）\n"
                "# 留空则不扩展。shinehe init 生成此空模板。\n",
                encoding="utf-8",
            )

        return created

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

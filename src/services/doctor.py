"""健康检查服务 — shinehe doctor

检查项目:
- 配置文件可读
- 数据目录可写
- SQLite / FTS5 / sqlite-vec 可用
- Embedding / LLM 端点可达（可选，带超时）
- Reranker 状态
- MCP 客户端配置 JSON 合法性

退出码:
  0 = 全部正常
  1 = 存在严重错误
  2 = 仅有警告
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import yaml

from src.utils.config import Config
from src.utils.paths import get_config_path, get_data_dir


class DoctorService:
    """系统健康检查服务"""

    def run_all_checks(
        self,
        config_path: str | None = None,
    ) -> list[dict[str, str]]:
        """执行所有健康检查

        Args:
            config_path: 自定义配置文件路径（None 时使用默认路径）

        Returns:
            检查结果列表，每项包含 name / status / message
            status: "ok" | "warn" | "fail"
        """
        results: list[dict[str, str]] = []
        results.extend(self.check_config(config_path))
        results.extend(self.check_knowledge_mode(config_path))
        results.extend(self.check_data_dir())
        results.extend(self.check_sqlite())
        results.extend(self.check_fts5())
        results.extend(self.check_sqlite_vec())
        results.extend(self.check_embedding_endpoint())
        results.extend(self.check_llm_endpoint())
        results.extend(self.check_reranker())
        results.extend(self.check_mcp_client_configs())
        return results

    # ------------------------------------------------------------------
    # 配置检查
    # ------------------------------------------------------------------

    def check_config(self, config_path: str | None = None) -> list[dict[str, str]]:
        """检查配置文件是否可读"""
        try:
            if config_path:
                path = Path(config_path)
            else:
                path = get_config_path()

            if not path.exists():
                return [self._result("config", "fail", f"配置文件不存在: {path}")]

            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            if not isinstance(data, dict):
                return [self._result("config", "fail", "配置文件格式错误（非 dict）")]

            return [self._result("config", "ok", f"配置文件可读: {path}")]
        except Exception as e:
            return [self._result("config", "fail", f"配置文件读取失败: {e}")]

    def check_knowledge_mode(
        self, config_path: str | None = None,
    ) -> list[dict[str, str]]:
        """检查知识运行档位（verified / authoring / evidence_only）。

        Phase 1：解析模式、弃用别名提示、读写开关一致性。
        Serving Claim 统计留给 Phase 2 WikiServingGate。
        """
        from src.utils.knowledge_mode import (
            InvalidKnowledgeModeError,
            describe_mode,
            resolve_knowledge_mode,
        )

        results: list[dict[str, str]] = []
        try:
            if config_path:
                path = Path(config_path)
            else:
                path = get_config_path()

            raw_mode = None
            wiki_cfg: dict = {}
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if isinstance(data, dict):
                    kw = data.get("knowledge_workflow") or {}
                    if isinstance(kw, dict):
                        raw_mode = kw.get("mode")
                    wiki_cfg = data.get("wiki") or {}
                    if not isinstance(wiki_cfg, dict):
                        wiki_cfg = {}
            else:
                # Fall back to live Config
                raw_mode = Config.get("knowledge_workflow.mode", None)
                wiki_cfg = {
                    "read_enabled": Config.get("wiki.read_enabled", None),
                    "authoring_enabled": Config.get("wiki.authoring_enabled", None),
                }

            try:
                info = describe_mode(raw_mode)
            except InvalidKnowledgeModeError as e:
                return [self._result("knowledge_mode", "fail", str(e))]

            resolved = info["resolved"]
            msg = f"知识模式: {resolved}"
            if info.get("legacy_alias"):
                msg += f"（配置仍为旧值 {raw_mode!r}，未改写文件）"
                results.append(self._result("knowledge_mode", "warn", msg))
                if info.get("deprecation_hint"):
                    results.append(
                        self._result(
                            "knowledge_mode_deprecation",
                            "warn",
                            str(info["deprecation_hint"]),
                        ),
                    )
            else:
                results.append(self._result("knowledge_mode", "ok", msg))

            read_flag = wiki_cfg.get("read_enabled")
            auth_flag = wiki_cfg.get("authoring_enabled")
            # Infer when flags absent (legacy configs)
            if read_flag is None:
                read_flag = resolved in ("verified", "authoring")
            if auth_flag is None:
                auth_flag = resolved == "authoring"

            results.append(
                self._result(
                    "wiki_read",
                    "ok",
                    f"Verified Wiki 读取: {'enabled' if read_flag else 'disabled'}",
                ),
            )
            results.append(
                self._result(
                    "wiki_authoring",
                    "ok",
                    f"Wiki Authoring: {'enabled' if auth_flag else 'disabled'}",
                ),
            )

            if auth_flag and resolved != "authoring":
                results.append(
                    self._result(
                        "wiki_authoring_consistency",
                        "warn",
                        "wiki.authoring_enabled=true 但 knowledge_workflow.mode "
                        f"解析为 {resolved}；写路径仍以 mode 为准",
                    ),
                )

            if wiki_cfg.get("auto_publish") is True:
                results.append(
                    self._result(
                        "wiki_auto_publish",
                        "warn",
                        "wiki.auto_publish=true：与默认安全策略冲突，建议关闭",
                    ),
                )
            else:
                results.append(
                    self._result(
                        "wiki_auto_publish",
                        "ok",
                        "自动发布: disabled（安全）",
                    ),
                )

            # Placeholder until Phase 2 Serving Gate
            results.append(
                self._result(
                    "serving_claims",
                    "ok",
                    "可 Serving Claim 统计：Phase 2 WikiServingGate 启用后提供",
                ),
            )
            # Touch resolve to keep import used if describe_mode changes
            _ = resolve_knowledge_mode(raw_mode)
            return results
        except Exception as e:
            return [
                self._result(
                    "knowledge_mode", "warn", f"知识模式检查异常: {e}",
                ),
            ]

    # ------------------------------------------------------------------
    # 数据目录检查
    # ------------------------------------------------------------------

    def check_data_dir(self) -> list[dict[str, str]]:
        """检查数据目录是否可写"""
        try:
            data_dir = get_data_dir()

            if not data_dir.exists():
                return [self._result("data_dir", "warn", f"数据目录不存在: {data_dir}")]

            test_file = data_dir / ".doctor_write_test"
            test_file.write_text("test", encoding="utf-8")
            test_file.unlink()

            return [self._result("data_dir", "ok", f"数据目录可写: {data_dir}")]
        except PermissionError as e:
            return [self._result("data_dir", "fail", f"数据目录无写入权限: {e}")]
        except Exception as e:
            return [self._result("data_dir", "warn", f"数据目录检查异常: {e}")]

    # ------------------------------------------------------------------
    # SQLite / FTS5 / sqlite-vec 检查
    # ------------------------------------------------------------------

    def check_sqlite(self) -> list[dict[str, str]]:
        """检查 SQLite 是否可用"""
        try:
            conn = sqlite3.connect(":memory:")
            version = conn.execute("SELECT sqlite_version()").fetchone()[0]
            conn.close()
            return [self._result("sqlite", "ok", f"SQLite 可用: v{version}")]
        except Exception as e:
            return [self._result("sqlite", "fail", f"SQLite 不可用: {e}")]

    def check_fts5(self) -> list[dict[str, str]]:
        """检查 FTS5 扩展是否可用"""
        try:
            conn = sqlite3.connect(":memory:")
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS _doctor_fts_test "
                "USING fts5(content)"
            )
            conn.execute("DROP TABLE IF EXISTS _doctor_fts_test")
            conn.close()
            return [self._result("fts5", "ok", "FTS5 扩展可用")]
        except Exception as e:
            return [self._result("fts5", "warn", f"FTS5 不可用: {e}")]

    def check_sqlite_vec(self) -> list[dict[str, str]]:
        """检查 sqlite-vec 扩展是否可用"""
        try:
            import sqlite_vec  # noqa: F401
            return [self._result("sqlite_vec", "ok", "sqlite-vec 可用")]
        except ImportError:
            return [self._result("sqlite_vec", "warn", "sqlite-vec 未安装")]
        except Exception as e:
            return [self._result("sqlite_vec", "warn", f"sqlite-vec 加载异常: {e}")]

    # ------------------------------------------------------------------
    # AI 端点检查
    # ------------------------------------------------------------------

    def check_embedding_endpoint(self) -> list[dict[str, str]]:
        """检查 Embedding 端点是否可达（可选，带 5s 超时）"""
        try:
            base_url = Config.get("embedding.base_url", "")
            if not base_url:
                return [self._result("embedding", "warn", "未配置 Embedding 端点")]
            return self._check_endpoint("embedding", base_url)
        except Exception as e:
            return [self._result("embedding", "warn", f"Embedding 检查异常: {e}")]

    def check_llm_endpoint(self) -> list[dict[str, str]]:
        """检查 LLM 端点是否可达（可选，带 5s 超时）"""
        try:
            base_url = Config.get("llm.base_url", "")
            if not base_url:
                return [self._result("llm", "warn", "未配置 LLM 端点")]
            return self._check_endpoint("llm", base_url)
        except Exception as e:
            return [self._result("llm", "warn", f"LLM 检查异常: {e}")]

    def _check_endpoint(self, name: str, base_url: str) -> list[dict[str, str]]:
        """使用 httpx 检查端点是否可达"""
        try:
            resp = httpx.get(base_url, timeout=5.0)
            if resp.status_code < 500:
                return [self._result(name, "ok", f"{name} 端点可达: {base_url}")]
            else:
                return [
                    self._result(
                        name, "warn",
                        f"{name} 端点返回 {resp.status_code}: {base_url}",
                    ),
                ]
        except ImportError:
            return [self._result(name, "warn", "httpx 未安装，跳过端点检查")]
        except Exception as e:
            return [self._result(name, "warn", f"{name} 端点不可达: {e}")]

    # ------------------------------------------------------------------
    # Reranker 检查
    # ------------------------------------------------------------------

    def check_reranker(self) -> list[dict[str, str]]:
        """检查 Reranker 配置状态"""
        try:
            enabled = Config.get("reranker.enabled", False)
            if not enabled:
                return [self._result("reranker", "ok", "Reranker 未启用（正常）")]

            base_url = Config.get("reranker.base_url", "")
            if not base_url:
                return [self._result("reranker", "warn", "Reranker 已启用但未配置 URL")]

            return [self._result("reranker", "ok", f"Reranker 已启用: {base_url}")]
        except Exception as e:
            return [self._result("reranker", "warn", f"Reranker 检查异常: {e}")]

    # ------------------------------------------------------------------
    # MCP 客户端配置检查
    # ------------------------------------------------------------------

    def check_mcp_client_configs(self) -> list[dict[str, str]]:
        """检查已知 MCP 客户端配置文件的 JSON 合法性"""
        from src.services.project_setup import get_agent_config_paths

        results: list[dict[str, str]] = []
        agent_paths = get_agent_config_paths()

        for name, path in agent_paths.items():
            if not path.exists():
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    results.append(
                        self._result(f"mcp_{name}", "ok", f"{name}: JSON 合法"),
                    )
                else:
                    results.append(
                        self._result(
                            f"mcp_{name}", "warn",
                            f"{name}: JSON 根节点非 dict",
                        ),
                    )
            except json.JSONDecodeError as e:
                results.append(
                    self._result(f"mcp_{name}", "fail", f"{name}: JSON 解析错误: {e}"),
                )
            except Exception as e:
                results.append(
                    self._result(f"mcp_{name}", "warn", f"{name}: 读取异常: {e}"),
                )

        if not results:
            results.append(
                self._result("mcp_clients", "ok", "未发现已安装的 MCP 客户端配置"),
            )

        return results

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _result(name: str, status: str, message: str) -> dict[str, str]:
        return {"name": name, "status": status, "message": message}

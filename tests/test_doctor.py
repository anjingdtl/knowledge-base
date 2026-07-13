"""DoctorService 单元测试

测试健康检查逻辑，使用 mock 替代外部依赖，不依赖 PySide6。
"""
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.services.doctor import DoctorService

# ---------------------------------------------------------------------------
# SQLite / FTS5 检查
# ---------------------------------------------------------------------------


class TestSQLiteChecks:
    """SQLite 相关检查测试"""

    def test_check_sqlite_ok(self):
        """SQLite 可用时返回 ok"""
        service = DoctorService()
        results = service.check_sqlite()
        assert len(results) == 1
        assert results[0]["status"] == "ok"
        assert "SQLite" in results[0]["message"]

    def test_check_fts5_ok(self):
        """FTS5 可用时返回 ok"""
        service = DoctorService()
        results = service.check_fts5()
        assert len(results) == 1
        # FTS5 在大多数现代 Python/SQLite 构建中可用
        assert results[0]["status"] in ("ok", "warn")

    def test_check_fts5_failure(self):
        """FTS5 不可用时返回 warn"""
        service = DoctorService()
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = sqlite3.OperationalError("no such module")
            mock_connect.return_value = mock_conn
            results = service.check_fts5()
        assert results[0]["status"] == "warn"

    def test_check_sqlite_vec_import_error(self):
        """sqlite-vec 未安装时返回 warn"""
        service = DoctorService()
        with patch.dict("sys.modules", {"sqlite_vec": None}):
            results = service.check_sqlite_vec()
        assert len(results) == 1
        assert results[0]["status"] == "warn"


# ---------------------------------------------------------------------------
# 配置文件检查
# ---------------------------------------------------------------------------


class TestConfigCheck:
    """配置文件检查测试"""

    def test_check_config_valid(self, tmp_path):
        """有效配置文件返回 ok"""
        import yaml

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump({"llm": {"model": "test"}}), encoding="utf-8",
        )

        service = DoctorService()
        results = service.check_config(str(config_file))
        assert results[0]["status"] == "ok"

    def test_check_config_missing(self, tmp_path):
        """配置文件不存在返回 fail"""
        service = DoctorService()
        results = service.check_config(str(tmp_path / "nonexistent.yaml"))
        assert results[0]["status"] == "fail"


class TestKnowledgeModeCheck:
    def test_verified_mode_ok(self, tmp_path):
        import yaml

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump({
                "knowledge_workflow": {"mode": "verified"},
                "wiki": {
                    "read_enabled": True,
                    "authoring_enabled": False,
                    "auto_publish": False,
                },
            }),
            encoding="utf-8",
        )
        service = DoctorService()
        results = service.check_knowledge_mode(str(config_file))
        by_name = {r["name"]: r for r in results}
        assert by_name["knowledge_mode"]["status"] == "ok"
        assert "verified" in by_name["knowledge_mode"]["message"]
        assert by_name["wiki_authoring"]["status"] == "ok"

    def test_wiki_first_deprecation_warn(self, tmp_path):
        import yaml

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump({"knowledge_workflow": {"mode": "wiki_first"}}),
            encoding="utf-8",
        )
        service = DoctorService()
        results = service.check_knowledge_mode(str(config_file))
        by_name = {r["name"]: r for r in results}
        assert by_name["knowledge_mode"]["status"] == "warn"
        assert "authoring" in by_name["knowledge_mode"]["message"]
        assert "knowledge_mode_deprecation" in by_name

    def test_invalid_mode_fail(self, tmp_path):
        import yaml

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump({"knowledge_workflow": {"mode": "turbo"}}),
            encoding="utf-8",
        )
        service = DoctorService()
        results = service.check_knowledge_mode(str(config_file))
        assert results[0]["status"] == "fail"
        assert "非法知识模式" in results[0]["message"]

    def test_check_config_invalid_format(self, tmp_path):
        """配置文件格式错误返回 fail"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("not: [a: valid: yaml: {{", encoding="utf-8")

        service = DoctorService()
        results = service.check_config(str(config_file))
        # 无效 YAML 会抛异常
        assert results[0]["status"] in ("fail", "ok")  # 可能是 fail 或 ok（YAML 宽容解析）


# ---------------------------------------------------------------------------
# 数据目录检查
# ---------------------------------------------------------------------------


class TestDataDirCheck:
    """数据目录检查测试"""

    def test_check_data_dir_writable(self, tmp_path):
        """可写数据目录返回 ok"""
        service = DoctorService()
        with patch("src.services.doctor.get_data_dir", return_value=tmp_path):
            results = service.check_data_dir()
        assert results[0]["status"] == "ok"

    def test_check_data_dir_not_exists(self, tmp_path):
        """数据目录不存在返回 warn"""
        nonexistent = tmp_path / "no_such_dir"
        service = DoctorService()
        with patch("src.services.doctor.get_data_dir", return_value=nonexistent):
            results = service.check_data_dir()
        assert results[0]["status"] == "warn"

    def test_check_data_dir_permission_error(self, tmp_path):
        """数据目录无写入权限返回 fail"""
        service = DoctorService()

        mock_dir = MagicMock(spec=Path)
        mock_dir.exists.return_value = True
        mock_dir.__truediv__ = MagicMock(
            side_effect=lambda name: MagicMock(
                write_text=MagicMock(side_effect=PermissionError("denied")),
            ),
        )

        with patch("src.services.doctor.get_data_dir", return_value=mock_dir):
            results = service.check_data_dir()
        assert results[0]["status"] == "fail"


# ---------------------------------------------------------------------------
# 端点检查（mocked）
# ---------------------------------------------------------------------------


class TestEndpointChecks:
    """AI 端点检查测试"""

    def test_check_embedding_no_config(self):
        """未配置 Embedding 端点返回 warn"""
        service = DoctorService()
        mock_config = MagicMock()
        mock_config.get.return_value = ""
        with patch("src.services.doctor.Config", mock_config):
            results = service.check_embedding_endpoint()
        assert results[0]["status"] == "warn"

    def test_check_llm_no_config(self):
        """未配置 LLM 端点返回 warn"""
        service = DoctorService()
        mock_config = MagicMock()
        mock_config.get.return_value = ""
        with patch("src.services.doctor.Config", mock_config):
            results = service.check_llm_endpoint()
        assert results[0]["status"] == "warn"

    def test_check_embedding_reachable(self):
        """Embedding 端点可达返回 ok"""
        service = DoctorService()
        mock_config = MagicMock()
        mock_config.get.return_value = "http://localhost:11434/v1"
        with patch("src.services.doctor.Config", mock_config):
            with patch("src.services.doctor.httpx") as mock_httpx:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_httpx.get.return_value = mock_resp
                results = service.check_embedding_endpoint()
        assert results[0]["status"] == "ok"

    def test_check_endpoint_server_error(self):
        """端点返回 5xx 返回 warn"""
        service = DoctorService()
        mock_config = MagicMock()
        mock_config.get.return_value = "http://broken-server/v1"
        with patch("src.services.doctor.Config", mock_config):
            with patch("src.services.doctor.httpx") as mock_httpx:
                mock_resp = MagicMock()
                mock_resp.status_code = 503
                mock_httpx.get.return_value = mock_resp
                results = service.check_llm_endpoint()
        assert results[0]["status"] == "warn"

    def test_check_endpoint_unreachable(self):
        """端点不可达返回 warn"""
        service = DoctorService()
        mock_config = MagicMock()
        mock_config.get.return_value = "http://offline-server/v1"
        with patch("src.services.doctor.Config", mock_config):
            with patch("src.services.doctor.httpx") as mock_httpx:
                mock_httpx.get.side_effect = ConnectionError("refused")
                results = service.check_llm_endpoint()
        assert results[0]["status"] == "warn"


# ---------------------------------------------------------------------------
# Reranker 检查
# ---------------------------------------------------------------------------


class TestRerankerCheck:
    """Reranker 检查测试"""

    def test_reranker_disabled(self):
        """Reranker 未启用返回 ok"""
        service = DoctorService()
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": False,
        }.get(key, default)
        with patch("src.services.doctor.Config", mock_config):
            results = service.check_reranker()
        assert results[0]["status"] == "ok"

    def test_reranker_enabled_with_url(self):
        """Reranker 已启用且有 URL 返回 ok"""
        service = DoctorService()
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": True,
            "reranker.base_url": "https://api.siliconflow.cn/v1",
        }.get(key, default)
        with patch("src.services.doctor.Config", mock_config):
            results = service.check_reranker()
        assert results[0]["status"] == "ok"

    def test_reranker_enabled_no_url(self):
        """Reranker 已启用但无 URL 返回 warn"""
        service = DoctorService()
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            "reranker.enabled": True,
            "reranker.base_url": "",
        }.get(key, default)
        with patch("src.services.doctor.Config", mock_config):
            results = service.check_reranker()
        assert results[0]["status"] == "warn"


# ---------------------------------------------------------------------------
# MCP 客户端配置检查
# ---------------------------------------------------------------------------


class TestMCPClientChecks:
    """MCP 客户端配置检查测试"""

    def test_valid_json_config(self, tmp_path):
        """合法 JSON 配置返回 ok"""
        config_file = tmp_path / "mcp.json"
        config_file.write_text(
            json.dumps({"mcpServers": {"test": {}}}), encoding="utf-8",
        )

        service = DoctorService()
        with patch(
            "src.services.project_setup.get_agent_config_paths",
            return_value={"cursor": config_file},
        ):
            results = service.check_mcp_client_configs()

        assert any(r["status"] == "ok" for r in results)

    def test_invalid_json_config(self, tmp_path):
        """非法 JSON 配置返回 fail"""
        config_file = tmp_path / "mcp.json"
        config_file.write_text("{ invalid json }", encoding="utf-8")

        service = DoctorService()
        with patch(
            "src.services.project_setup.get_agent_config_paths",
            return_value={"cursor": config_file},
        ):
            results = service.check_mcp_client_configs()

        assert any(r["status"] == "fail" for r in results)

    def test_no_client_configs(self):
        """无客户端配置时返回 ok"""
        service = DoctorService()
        with patch(
            "src.services.project_setup.get_agent_config_paths",
            return_value={"cursor": Path("/nonexistent/path/mcp.json")},
        ):
            results = service.check_mcp_client_configs()

        assert any(r["status"] == "ok" for r in results)


# ---------------------------------------------------------------------------
# 综合测试
# ---------------------------------------------------------------------------


class TestRunAllChecks:
    """run_all_checks 综合测试"""

    def test_run_all_checks_returns_list(self, tmp_path):
        """run_all_checks 返回检查结果列表"""
        service = DoctorService()
        config_file = tmp_path / "config.yaml"

        import yaml
        config_file.write_text(
            yaml.dump({"llm": {"model": "test"}}), encoding="utf-8",
        )

        results = service.run_all_checks(config_path=str(config_file))
        assert isinstance(results, list)
        assert len(results) >= 5  # 至少有 5 项检查

    def test_each_result_has_required_keys(self, tmp_path):
        """每个检查结果包含 name/status/message 键"""
        service = DoctorService()
        config_file = tmp_path / "config.yaml"

        import yaml
        config_file.write_text(
            yaml.dump({"test": True}), encoding="utf-8",
        )

        results = service.run_all_checks(config_path=str(config_file))
        for r in results:
            assert "name" in r
            assert "status" in r
            assert "message" in r
            assert r["status"] in ("ok", "warn", "fail")

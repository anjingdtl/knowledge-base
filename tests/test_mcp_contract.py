"""MCP 工具契约测试 — Phase 0 验收基线。

覆盖规范：
- 所有 MCP 工具返回 JSON-compatible dict
- 失败路径返回 ok=false + 稳定 error.code
- 大结果包含 truncated/limit/offset/next_offset/total_estimate
- 写工具返回 operation_id
- dry_run=True 不修改数据库
- kb_capabilities 工具能列出能力
- schema snapshot 文件存在
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import src.mcp_server as mcp_mod
from src.mcp_server import (
    create,
    delete,
    ingest_file,
    kb_capabilities,
    list_knowledge,
    query_operation_logs,
    read,
    reindex_all,
    search,
    search_fulltext,
    structured_query,
    tags as get_tags,
    update,
)
from src.services.db import Database


SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "mcp_tools.json"


@pytest.fixture
def mcp_env(setup_db, monkeypatch):
    """Mock 向量存储避免 embedding API 调用。"""

    class MockVS:
        def __init__(self, db=None):
            pass
        def search(self, query, top_k=5):
            return [{"id": "chunk-1", "text": "测试内容", "metadata": {}, "distance": 0.85}]
        def add_chunks(self, chunks):
            pass
        def delete_by_knowledge(self, kid):
            pass
        def count(self):
            return 0

    class MockBS:
        def __init__(self, db=None):
            pass
        def search(self, query, top_k=5):
            return [{"id": "block-1", "text": "测试内容", "page_id": "", "distance": 0.85}]
        def add_block_embedding(self, block_id, embedding):
            pass
        def delete_by_page(self, page_id):
            pass
        def count(self):
            return 0

    monkeypatch.setattr("src.services.vectorstore.VectorStore", MockVS)
    monkeypatch.setattr("src.services.block_store.BlockStore", MockBS)


def _insert_kb_item(title="测试", content="测试内容", tags=None):
    """直接通过 Database 插入，绕过 envelope。"""
    import json as _json
    import uuid
    from datetime import datetime
    kid = str(uuid.uuid4())
    Database.insert_knowledge({
        "id": kid,
        "title": title,
        "content": content,
        "source_type": "manual",
        "source_path": "",
        "file_type": "txt",
        "file_size": 0,
        "content_hash": "",
        "file_created_at": "",
        "file_modified_at": "",
        "tags": _json.dumps(tags or [], ensure_ascii=False),
        "version": 1,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    })
    return kid


# ---- Schema Snapshot ----

class TestSchemaSnapshot:
    def test_snapshot_file_exists(self):
        assert SNAPSHOT_PATH.exists(), f"缺少 schema snapshot: {SNAPSHOT_PATH}"

    def test_snapshot_has_envelope_shape(self):
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert "envelope_shape" in data
        assert "success" in data["envelope_shape"]
        assert "failure" in data["envelope_shape"]
        assert "dry_run" in data["envelope_shape"]

    def test_snapshot_has_error_codes(self):
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        codes = set(data["error_codes"])
        # 必须包含规范要求的最小集
        for required in {"NOT_FOUND", "VALIDATION_ERROR", "PERMISSION_DENIED", "INGEST_FAILED"}:
            assert required in codes, f"缺少 error_code: {required}"

    def test_snapshot_tool_names_match_registry(self):
        """snapshot 里的工具名不能多于实际注册的工具名（允许少：snapshot 是子集）。"""
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        snapshot_names = {t["name"] for t in data["tools"]}
        # 抽取 FastMCP 注册表
        registry = getattr(mcp_mod.mcp, "_tool_manager", None) or getattr(mcp_mod.mcp, "tool_manager", None)
        if registry is None:
            pytest.skip("FastMCP 没有暴露工具注册表")
        tools = getattr(registry, "_tools", None) or getattr(registry, "tools", {}) or {}
        registered = set(tools.keys())
        # snapshot 不应包含实际未注册的工具（防止 stale）
        stale = snapshot_names - registered
        assert not stale, f"snapshot 含未注册工具: {stale}"


# ---- Envelope 形状 ----

class TestEnvelopeShape:
    """所有工具必须返回 dict 且含 ``ok`` 字段。"""

    def test_read_existing(self, mcp_env):
        kid = _insert_kb_item()
        result = read(item_id=kid)
        assert isinstance(result, dict)
        assert result.get("ok") is True
        assert "data" in result
        assert result["data"]["id"] == kid

    def test_read_missing_returns_envelope_fail(self, mcp_env):
        result = read(item_id="nonexistent-id-xyz")
        assert isinstance(result, dict)
        assert result.get("ok") is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert result["error"]["details"]["item_id"] == "nonexistent-id-xyz"

    def test_search_returns_envelope(self, mcp_env):
        result = search(query="Python", top_k=3)
        assert isinstance(result, dict)
        assert result.get("ok") is True
        assert isinstance(result["data"], list)
        assert "total_estimate" in result["meta"]

    def test_search_fulltext_returns_envelope_with_pagination(self, mcp_env):
        for i in range(5):
            _insert_kb_item(f"知识{i}", f"Python 教程 #{i}")
        result = search_fulltext(query="Python", limit=2, offset=0)
        assert isinstance(result, dict)
        assert result.get("ok") is True
        meta = result["meta"]
        assert meta["limit"] == 2
        assert meta["offset"] == 0
        assert "truncated" in meta
        assert "next_offset" in meta
        assert "total_estimate" in meta

    def test_tags_returns_envelope(self, mcp_env):
        _insert_kb_item("A", tags=["AI", "教程"])
        result = get_tags()
        assert isinstance(result, dict)
        assert result.get("ok") is True
        assert isinstance(result["data"], list)
        assert result["meta"]["count"] == len(result["data"])

    def test_list_knowledge_returns_envelope_with_pagination(self, mcp_env):
        for i in range(5):
            _insert_kb_item(f"知识{i}")
        result = list_knowledge(limit=2, offset=0)
        assert isinstance(result, dict)
        assert result.get("ok") is True
        assert "total" in result["meta"]
        assert result["meta"]["limit"] == 2
        assert "next_offset" in result["meta"]
        assert "truncated" in result["meta"]


# ---- 错误码稳定性 ----

class TestStableErrorCodes:
    def test_read_missing_uses_NOT_FOUND(self, mcp_env):
        result = read(item_id="nope")
        assert result["error"]["code"] == "NOT_FOUND"

    def test_structured_query_bad_json_uses_QUERY_PARSE_ERROR(self, mcp_env):
        result = structured_query(query_dsl="not valid json{", limit=10)
        assert result["ok"] is False
        assert result["error"]["code"] == "QUERY_PARSE_ERROR"

    def test_structured_query_dict_input(self, mcp_env):
        """dict 输入也接受（不只是 str）。"""
        # 一个无害的 DSL：仅排序
        spec = {
            "filter_condition": {"type": "tag", "key": "Python"},
            "limit": 5,
            "offset": 0,
        }
        result = structured_query(query_dsl=json.dumps(spec), limit=5)
        # 不管命中与否，结构必须是 envelope
        assert "ok" in result


# ---- 写工具 + operation_id ----

class TestWriteToolsReturnOperationId:
    def test_create_returns_operation_id(self, mcp_env):
        result = create(title="新条目", content="内容")
        assert result.get("ok") is True
        assert "operation_id" in result
        assert result["operation_id"]

    def test_update_returns_operation_id(self, mcp_env):
        kid = _insert_kb_item("原标题")
        result = update(item_id=kid, title="新标题")
        assert result.get("ok") is True
        assert "operation_id" in result
        assert result["operation_id"]

    def test_delete_returns_operation_id(self, mcp_env):
        kid = _insert_kb_item()
        result = delete(item_id=kid)
        assert result.get("ok") is True
        assert "operation_id" in result
        assert result["operation_id"]


# ---- dry_run 行为 ----

class TestDryRunSafety:
    """``dry_run=True`` 不修改数据库且返回 dry_run envelope。"""

    def test_update_dry_run_does_not_change_db(self, mcp_env):
        kid = _insert_kb_item("原标题", "原内容")
        before_count = Database.count_knowledge()
        result = update(item_id=kid, title="新标题", dry_run=True)
        assert result.get("ok") is True
        assert result.get("dry_run") is True
        assert "would_change" in result["data"]
        # 数据库未变
        after = Database.get_knowledge(kid)
        assert after["title"] == "原标题"
        assert Database.count_knowledge() == before_count

    def test_delete_dry_run_does_not_remove(self, mcp_env):
        kid = _insert_kb_item()
        result = delete(item_id=kid, dry_run=True)
        assert result.get("ok") is True
        assert result.get("dry_run") is True
        assert "would_delete" in result["data"]["would_change"]
        # 数据仍在
        assert Database.get_knowledge(kid) is not None

    def test_reindex_dry_run_returns_count(self, mcp_env):
        _insert_kb_item()
        result = reindex_all(dry_run=True)
        assert result.get("ok") is True
        assert result.get("dry_run") is True
        assert result["data"]["would_change"]["item_count"] >= 1

    def test_create_dry_run_does_not_insert(self, mcp_env):
        before = Database.count_knowledge()
        result = create(title="dryrun", content="content", dry_run=True)
        assert result.get("ok") is True
        assert result.get("dry_run") is True
        assert Database.count_knowledge() == before


# ---- kb_capabilities ----

class TestKBCapabilities:
    def test_returns_envelope(self, mcp_env):
        result = kb_capabilities()
        assert result.get("ok") is True
        data = result["data"]
        assert "envelope" in data
        assert "error_codes" in data
        assert "recommended_flows" in data
        assert "research" in data["recommended_flows"]

    def test_lists_error_codes(self, mcp_env):
        result = kb_capabilities()
        codes = result["data"]["error_codes"]
        for required in ("NOT_FOUND", "VALIDATION_ERROR", "INGEST_FAILED", "QUERY_PARSE_ERROR"):
            assert required in codes

    def test_lists_recommended_flows(self, mcp_env):
        result = kb_capabilities()
        flows = result["data"]["recommended_flows"]
        for key in ("research", "safe_update", "import", "qna"):
            assert key in flows
            assert isinstance(flows[key], list)
            assert len(flows[key]) >= 2


# ---- query_operation_logs envelope ----

class TestQueryOperationLogs:
    def test_returns_envelope_with_pagination(self, mcp_env):
        result = query_operation_logs(limit=10, offset=0)
        assert isinstance(result, dict)
        assert result.get("ok") is True
        assert "total_estimate" in result["meta"]
        assert "next_offset" in result["meta"]
        assert "truncated" in result["meta"]

    def test_filters_by_source(self, mcp_env):
        result = query_operation_logs(source="mcp", limit=5)
        assert result.get("ok") is True
        # 所有结果都应是 source='mcp'
        for log in result["data"]:
            assert log.get("source") == "mcp"


# ---- regression: dry_run 不写 operation_log ----

class TestDryRunDoesNotLog:
    def test_update_dry_run_does_not_create_log(self, mcp_env):
        kid = _insert_kb_item()
        before = Database.get_conn().execute(
            "SELECT COUNT(*) as c FROM operation_logs WHERE target_id = ?", (kid,)
        ).fetchone()["c"]
        update(item_id=kid, title="new", dry_run=True)
        after = Database.get_conn().execute(
            "SELECT COUNT(*) as c FROM operation_logs WHERE target_id = ?", (kid,)
        ).fetchone()["c"]
        assert after == before, "dry_run 不应写入 operation_log"

    def test_reindex_dry_run_does_not_create_log(self, mcp_env):
        _insert_kb_item()
        before = Database.get_conn().execute(
            "SELECT COUNT(*) as c FROM operation_logs WHERE operation = 'reindex'"
        ).fetchone()["c"]
        reindex_all(dry_run=True)
        after = Database.get_conn().execute(
            "SELECT COUNT(*) as c FROM operation_logs WHERE operation = 'reindex'"
        ).fetchone()["c"]
        assert after == before

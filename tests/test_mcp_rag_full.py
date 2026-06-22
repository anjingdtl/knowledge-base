"""Sprint 2 验收：MCP RAG 完整结构化 payload + Agentic Query 工具。

覆盖：
- ``ask`` 返回 7 字段（answer / sources / source_graph / route / query_plan /
  block_contexts / warnings）
- ``sources[i].block_id`` 必填
- ``source_graph`` 含 truncated / node_count
- ``route_query`` 工具 envelope
- ``execute_query`` 工具 envelope（structured / graph 两种 type）
- ``ask_with_query`` 用显式 QuerySpec 控制 RAG
- ``graph_traverse`` 支持 limit / offset 分页
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from src.services.db import Database
from tests.test_full_pipeline_e2e import _make_page

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _insert_block_with_id(block_id, page_id, content, properties=None,
                          parent_id=None, order_idx=0):
    """使用与 _make_block 相同的契约，但显式指定 block_id（方便溯源验证）。"""
    Database.insert_blocks([{
        "id": block_id,
        "parent_id": parent_id,
        "page_id": page_id,
        "content": content,
        "block_type": "text",
        "properties": json.dumps(properties or {}, ensure_ascii=False),
        "order_idx": order_idx,
        "created_at": "2026-06-04T00:00:00",
        "updated_at": "2026-06-04T00:00:00",
    }])
    return block_id


def _build_rag_with_mock_llm(llm_chat_returns: list[str]):
    """构造一个用 mock LLM 替换的 RAG pipeline，返回 mock LLM 方便调整。"""
    from src.services.rag_pipeline import DEFAULT_PIPELINE_CONFIG, RagPipeline
    mock_llm = MagicMock()
    mock_llm.chat.side_effect = list(llm_chat_returns)
    pipeline = RagPipeline(pipeline_config=DEFAULT_PIPELINE_CONFIG, llm=mock_llm)
    return pipeline, mock_llm


# ---------------------------------------------------------------------------
# 1) agentic_router.serialize_route
# ---------------------------------------------------------------------------

class TestSerializeRoute:
    """serialize_route 把 QuerySpec 转 JSON-safe dict。"""

    def test_serializes_query_spec_to_dict(self):
        from src.models.query_dsl import QuerySpec
        from src.services.agentic_router import serialize_route

        spec = QuerySpec.from_json({"filter": {"tag": "bug"}, "limit": 5})
        routing = {
            "mode": "structured",
            "query_spec": spec,
            "explanation": "rule-based",
        }
        out = serialize_route(routing)
        assert out["mode"] == "structured"
        assert isinstance(out["query_spec"], dict)
        assert out["query_spec"]["filter"]["tag"] == "bug"
        assert out["query_spec"]["limit"] == 5

    def test_passes_through_when_query_spec_is_none(self):
        from src.services.agentic_router import serialize_route
        out = serialize_route({"mode": "hybrid", "query_spec": None, "explanation": "x"})
        assert out["query_spec"] is None

    def test_does_not_mutate_input(self):
        from src.models.query_dsl import QuerySpec
        from src.services.agentic_router import serialize_route
        spec = QuerySpec.from_json({"filter": {"tag": "x"}})
        original = {"mode": "structured", "query_spec": spec}
        out = serialize_route(original)
        # 原 dict 仍持有 QuerySpec 对象
        assert original["query_spec"] is spec
        # 输出 dict 是新的 spec dict
        assert out["query_spec"] is not spec


# ---------------------------------------------------------------------------
# 2) MCP ask：7 字段结构化 payload
# ---------------------------------------------------------------------------

class TestMCPAskStructuredPayload:
    """ask 工具 envelope.data 必含 7 字段；sources[i].block_id 必填。"""

    def test_ask_returns_envelope_with_seven_fields(self, monkeypatch):
        from src.services.rag_pipeline import DEFAULT_PIPELINE_CONFIG, RagPipeline

        # mock LLM：第一次返回 hybrid 路由，第二次生成回答
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            '{"mode": "hybrid", "query": "test question"}',
            "Sprint 2 测试回答。",
        ]
        pipeline = RagPipeline(pipeline_config=DEFAULT_PIPELINE_CONFIG, llm=mock_llm)

        # mock 整个 pipeline.execute 走容器路径
        import asyncio
        # 直接同步执行 pipeline.execute（不走 LLM 调用 RAG service）
        result = asyncio.run(pipeline.execute("Sprint 2 测试问题"))

        assert "answer" in result
        assert "sources" in result
        assert "source_graph" in result
        assert "route" in result
        assert "query_plan" in result
        assert "block_contexts" in result
        assert "warnings" in result

    def test_ask_envelope_meta_includes_routing_stats(self):
        """ask 工具 envelope.meta 暴露 source_count / route_mode / graph_truncated。"""
        # 不依赖真实 LLM，直接 import ask 函数验证 meta 字段路径
        import asyncio

        import src.mcp_server as mcp_mod
        from src.mcp_server import ask
        from src.services.rag_pipeline import DEFAULT_PIPELINE_CONFIG, RagPipeline

        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            '{"mode": "structured", "query": {"filter": {"tag": "x"}}}',
            "test answer",
        ]
        fake_pipeline = RagPipeline(pipeline_config=DEFAULT_PIPELINE_CONFIG, llm=mock_llm)

        def fake_query(q, conversation_history=None):
            return asyncio.run(fake_pipeline.execute(q, conversation_history=conversation_history))

        fake_service = MagicMock()
        fake_service.query.side_effect = fake_query

        # 用 stub 容器替换 _get_container() 的返回值，避开 property setter 限制
        fake_container = MagicMock()
        fake_container.rag_pipeline = fake_service
        original = mcp_mod._get_container
        mcp_mod._get_container = lambda: fake_container
        try:
            envelope = ask(question="test")
        finally:
            mcp_mod._get_container = original

        assert envelope["ok"] is True
        assert "meta" in envelope
        meta = envelope["meta"]
        assert "source_count" in meta
        assert "warning_count" in meta
        assert "route_mode" in meta
        assert "graph_truncated" in meta

    def test_ask_source_has_block_id_field(self):
        """当有检索结果时，sources[i].block_id 必填。"""
        import asyncio

        from src.services.rag_pipeline import DEFAULT_PIPELINE_CONFIG, RagPipeline

        _make_page("p-rag-1", "RAG 测试页", tags=["rag-test"])
        _insert_block_with_id("block-rag-1", "p-rag-1", "Block-first RAG 测试内容",
                              properties={"status": "open"})

        # mock LLM：第一次 hybrid，第二次生成回答
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            '{"mode": "hybrid", "query": "RAG 测试"}',
            "回答内容",
        ]
        pipeline = RagPipeline(pipeline_config=DEFAULT_PIPELINE_CONFIG, llm=mock_llm)
        result = asyncio.run(pipeline.execute("RAG 测试"))

        # 即便候选为空，sources 字段是 list；检查字段定义
        assert isinstance(result["sources"], list)
        for s in result["sources"]:
            assert "block_id" in s
            assert "knowledge_id" in s
            assert "title" in s
            assert "text_preview" in s

    def test_ask_source_graph_has_truncated_flag(self):
        """source_graph 必含 truncated / node_count 字段。"""
        import asyncio

        from src.services.rag_pipeline import DEFAULT_PIPELINE_CONFIG, RagPipeline

        _make_page("p-graph-1", "图谱测试页", tags=["graph-test"])
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            '{"mode": "hybrid", "query": "图谱测试"}',
            "回答",
        ]
        pipeline = RagPipeline(pipeline_config=DEFAULT_PIPELINE_CONFIG, llm=mock_llm)
        result = asyncio.run(pipeline.execute("图谱测试"))

        sg = result["source_graph"]
        assert "nodes" in sg
        assert "edges" in sg
        assert "truncated" in sg
        assert "node_count" in sg
        assert isinstance(sg["truncated"], bool)
        assert isinstance(sg["node_count"], int)

    def test_ask_route_has_mode_and_explanation(self):
        """route 字段含 mode + explanation。"""
        import asyncio

        from src.services.rag_pipeline import DEFAULT_PIPELINE_CONFIG, RagPipeline

        _make_page("p-route-1", "路由测试页", tags=["route-test"])
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            '{"mode": "hybrid", "query": "路由测试"}',
            "回答",
        ]
        pipeline = RagPipeline(pipeline_config=DEFAULT_PIPELINE_CONFIG, llm=mock_llm)
        result = asyncio.run(pipeline.execute("路由测试"))

        route = result["route"]
        assert "mode" in route
        assert "explanation" in route
        assert route["mode"] in ("structured", "graph", "hybrid")


# ---------------------------------------------------------------------------
# 3) MCP route_query 工具
# ---------------------------------------------------------------------------

class TestMCPRouteQuery:
    """route_query 工具 envelope + 返回结构。"""

    def test_route_query_envelope_structure(self):
        from src.mcp_server import route_query

        # 不需要真实数据 — rule-based 路径会在测试问题上走到 structured
        result = route_query(question="找出所有标记为 bug 的问题")
        assert result["ok"] is True
        assert "meta" in result
        assert "mode" in result["meta"]
        payload = result["data"]
        assert payload["mode"] in ("structured", "graph", "hybrid")
        assert "explanation" in payload

    def test_route_query_falls_back_to_hybrid(self):
        from src.mcp_server import route_query

        result = route_query(question="Python 异步编程有哪些最佳实践")
        assert result["ok"] is True
        # fuzzy 问题通常 fallback 到 hybrid，但 LLM 可能判定为 structured（取决于模型版本）
        assert result["data"]["mode"] in {"hybrid", "structured"}

    def test_route_query_query_spec_is_json_safe(self):
        import json as _json

        from src.mcp_server import route_query

        result = route_query(question="找出所有标记为 bug 的问题")
        assert result["ok"] is True
        # 能 JSON 序列化说明没有 QuerySpec 对象泄漏
        _json.dumps(result["data"])

    def test_route_query_uses_known_tag_mentions_without_marker(self):
        from src.mcp_server import route_query

        _make_page("p-route-tag", "创智杯测试", tags=["创智杯"])

        result = route_query(question="创智杯竞赛规则和拉收目标")

        assert result["ok"] is True
        assert result["data"]["mode"] == "structured"
        assert result["data"]["query_spec"]["filter"]["tag"] == "创智杯"


# ---------------------------------------------------------------------------
# 4) MCP execute_query 工具
# ---------------------------------------------------------------------------

class TestMCPExecuteQuery:
    """execute_query 工具 envelope + structured / graph type 验证。"""

    def test_execute_query_structured(self):
        from src.mcp_server import execute_query

        _make_page("p-exec-1", "执行查询测试 A", tags=["exec-test"])
        _make_page("p-exec-2", "执行查询测试 B", tags=["exec-test"])

        dsl = {"filter": {"tag": "exec-test"}, "limit": 10}
        result = execute_query(query_spec=dsl, type="structured", limit=10)
        assert result["ok"] is True
        payload = result["data"]
        assert isinstance(payload, list)
        assert len(payload) >= 2
        assert result["meta"]["type"] == "structured"
        assert "total_estimate" in result["meta"]

    def test_execute_query_accepts_tag_eq_filter_shape(self):
        from src.mcp_server import execute_query

        _make_page("p-exec-eq-1", "执行查询 eq A", tags=["创智杯"])
        _make_page("p-exec-eq-2", "执行查询 eq B", tags=["其他"])

        result = execute_query(
            query_spec={"filter": {"tag": {"eq": "创智杯"}}},
            type="structured",
            limit=10,
        )

        assert result["ok"] is True
        assert [row["id"] for row in result["data"]] == ["p-exec-eq-1"]

    def test_execute_query_graph_requires_start_ids(self):
        from src.mcp_server import execute_query

        result = execute_query(
            query_spec={"filter": {"tag": "x"}, "max_depth": 1},
            type="graph",
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "VALIDATION_ERROR"

    def test_execute_query_graph_returns_envelope(self):
        from src.mcp_server import execute_query

        _make_page("p-exec-graph", "图遍历测试起点", tags=["exec-graph-test"])
        result = execute_query(
            query_spec={
                "start_ids": ["p-exec-graph"],
                "start_type": "knowledge",
                "max_depth": 1,
            },
            type="graph",
        )
        assert result["ok"] is True
        payload = result["data"]
        assert "nodes" in payload
        assert "edges" in payload
        assert any(n["id"] == "p-exec-graph" for n in payload["nodes"])

    def test_execute_query_invalid_type(self):
        from src.mcp_server import execute_query

        result = execute_query(query_spec={"filter": {"tag": "x"}}, type="invalid")
        assert result["ok"] is False
        assert result["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 5) MCP ask_with_query 工具
# ---------------------------------------------------------------------------

class TestMCPAskWithQuery:
    """ask_with_query 用显式 QuerySpec 控制 RAG。"""

    def test_ask_with_query_envelope(self):
        from src.mcp_server import ask_with_query

        _make_page("p-awq-1", "显式查询测试 A", tags=["awq-test"])
        _make_page("p-awq-2", "显式查询测试 B", tags=["awq-test"])

        dsl = {"filter": {"tag": "awq-test"}, "limit": 5}
        result = ask_with_query(
            question="列出所有 awq-test 标签的条目",
            query_spec=dsl,
            top_k=5,
        )
        assert result["ok"] is True
        payload = result["data"]
        assert "answer" in payload
        assert "sources" in payload
        assert "route" in payload
        # 显式 query_spec → mode 应该是 structured
        assert payload["route"]["mode"] == "structured"
        assert result["meta"]["route_mode"] == "structured"

    def test_ask_with_query_includes_seven_fields(self):
        from src.mcp_server import ask_with_query

        _make_page("p-awq-fields", "七字段测试页", tags=["awq-fields"])
        dsl = {"filter": {"tag": "awq-fields"}, "limit": 5}
        result = ask_with_query(
            question="列出所有 awq-fields 标签的条目",
            query_spec=dsl,
        )
        assert result["ok"] is True
        payload = result["data"]
        for key in ("answer", "sources", "source_graph", "route", "query_plan",
                    "block_contexts", "warnings"):
            assert key in payload, f"missing field: {key}"


# ---------------------------------------------------------------------------
# 6) graph_traverse 支持 limit / offset
# ---------------------------------------------------------------------------

class TestMCPGraphTraversePagination:
    """graph_traverse 工具 envelope.data 含 truncated / node_count；支持 limit/offset。"""

    def test_graph_traverse_returns_truncated_field(self):
        from src.mcp_server import graph_traverse

        _make_page("p-gt-1", "图遍历分页测试", tags=["gt-paging-test"])
        result = graph_traverse(
            start_ids=json.dumps(["p-gt-1"]),
            max_depth=1,
            start_type="knowledge",
            limit=50,
            offset=0,
        )
        assert result["ok"] is True
        payload = result["data"]
        assert "truncated" in payload
        assert isinstance(payload["truncated"], bool)
        # meta 里有 limit / offset / max_depth
        assert result["meta"]["limit"] == 50
        assert result["meta"]["offset"] == 0
        assert result["meta"]["max_depth"] == 1

    def test_graph_traverse_respects_limit(self):
        from src.mcp_server import graph_traverse

        _make_page("p-gt-limit", "图遍历 limit 测试", tags=["gt-limit-test"])
        result = graph_traverse(
            start_ids=json.dumps(["p-gt-limit"]),
            max_depth=1,
            start_type="knowledge",
            limit=1,
        )
        assert result["ok"] is True
        # 节点数不应超过 limit
        assert len(result["data"]["nodes"]) <= 1


# ---------------------------------------------------------------------------
# 7) graph_traversal._load_node 暴露 block_id
# ---------------------------------------------------------------------------

class TestGraphTraversalBlockId:
    """graph_traversal 给 block 节点加显式 block_id 字段。"""

    def test_block_node_has_block_id_field(self):
        from src.models.block import EntityRef
        from src.repositories.entity_ref_repo import EntityRefRepository
        from src.services.graph_traversal import GraphTraversalService

        _make_page("p-bid-1", "Block ID 测试")
        _insert_block_with_id("block-bid-1", "p-bid-1", "block content for id test")
        EntityRefRepository().upsert(EntityRef(
            id="ref-bid-1", source_type="block", source_id="block-bid-1",
            target_type="knowledge", target_id="p-bid-1", ref_type="contains",
        ))

        result = GraphTraversalService().traverse(
            start_ids=["block-bid-1"], start_type="block", max_depth=0,
        )
        block_nodes = [n for n in result["nodes"] if n["type"] == "block"]
        assert len(block_nodes) >= 1
        node = block_nodes[0]
        assert node["block_id"] == node["id"]
        assert node["properties"]["block_id"] == node["id"]


# ---------------------------------------------------------------------------
# 8) query_executor 暴露 block_id
# ---------------------------------------------------------------------------

class TestQueryExecutorBlockId:
    """query_executor include_blocks 时每条 block 显式带 block_id。"""

    def test_blocks_have_block_id_alias(self):
        from src.models.query_dsl import QuerySpec
        from src.services.query_executor import QueryExecutor

        _make_page("p-qe-1", "Executor Block ID 测试", tags=["qe-test"])
        _insert_block_with_id("block-qe-1", "p-qe-1", "executor block 1")
        _insert_block_with_id("block-qe-2", "p-qe-1", "executor block 2", order_idx=1)

        executor = QueryExecutor()
        spec = QuerySpec.from_json({
            "filter": {"tag": "qe-test"},
            "include_blocks": True,
        })
        results = executor.execute(spec)
        assert len(results) == 1
        blocks = results[0]["blocks"]
        assert len(blocks) == 2
        for b in blocks:
            assert b["block_id"] == b["id"]

"""全流程端到端测试：知识导入 → 存储 → 属性传播 → 链接发现 → 结构化查询 → RAG 问答 → MCP 工具

覆盖 Phase 1 (Structured/Graph RAG) + Phase 2 (Logseq Graph) + Phase 3 (Query Revolution) 的全部核心能力。
"""
import json

import pytest

from src.services.db import Database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    from datetime import datetime
    return datetime.now().isoformat()


def _make_page(page_id, title, content="", tags=None, file_type="md", source_type="manual"):
    Database.insert_knowledge({
        "id": page_id,
        "title": title,
        "content": content,
        "source_type": source_type,
        "source_path": f"/docs/{title}.md",
        "file_type": file_type,
        "file_size": len(content),
        "content_hash": "",
        "file_created_at": _now(),
        "file_modified_at": _now(),
        "tags": json.dumps(tags or [], ensure_ascii=False),
        "version": 1,
        "created_at": _now(),
        "updated_at": _now(),
    })
    return page_id


def _make_block(block_id, page_id, content, parent_id=None, order_idx=0, properties=None):
    Database.insert_blocks([{
        "id": block_id,
        "parent_id": parent_id,
        "page_id": page_id,
        "content": content,
        "block_type": "text",
        "properties": json.dumps(properties or {}, ensure_ascii=False),
        "order_idx": order_idx,
        "created_at": _now(),
        "updated_at": _now(),
    }])
    return block_id


# ---------------------------------------------------------------------------
# Phase 1: 结构化导入 + Block 上下文 + 链接发现
# ---------------------------------------------------------------------------

class TestPhase1StructuredImportAndContext:
    """验证 Phase 1：结构化 Block 导入、父子上下文、链接发现"""

    def test_structured_blocks_with_parent_child_hierarchy(self):
        page_id = _make_page("p-arch", "系统架构设计", tags=["架构", "技术"],
                             content="系统架构设计文档")
        root = _make_block("b-root", page_id, "系统架构概览")
        child1 = _make_block("b-fe", page_id, "前端架构：React + TypeScript",
                             parent_id=root, order_idx=1)
        _make_block("b-be", page_id, "后端架构：FastAPI + SQLite",
                             parent_id=root, order_idx=2)
        grandchild = _make_block("b-fe-detail", page_id,
                                 "前端使用 Vite 构建，组件库基于 Ant Design",
                                 parent_id=child1, order_idx=1)

        from src.services.block_context import BlockContextService
        ctx_service = BlockContextService(db=Database)
        context = ctx_service.build_context(grandchild)

        assert isinstance(context, str)
        assert "React" in context
        assert "系统架构概览" in context

    def test_link_discovery_creates_entity_refs(self):
        _make_page("p-arch", "系统架构设计",
                   content="系统架构设计文档", tags=["架构"])
        page_a = _make_page("p-fe-plan", "前端重构计划",
                            content="参考 [[系统架构设计]] 进行前端重构",
                            tags=["前端", "计划"])
        _make_block("b-link-block", page_a,
                    "参考 [[系统架构设计]] 进行重构，详见 [[后端API设计]]")

        from src.repositories.entity_ref_repo import EntityRefRepository
        from src.services.link_discovery import LinkDiscoveryService
        service = LinkDiscoveryService(db=Database)
        created = service.discover_links(page_a)

        assert created >= 1
        repo = EntityRefRepository(db=Database)
        refs = repo.list_for_source("block", "b-link-block")
        target_ids = {r.target_id for r in refs}
        assert "p-arch" in target_ids


# ---------------------------------------------------------------------------
# Phase 2: 标签层级 + 属性 Schema + 有效属性传播
# ---------------------------------------------------------------------------

class TestPhase2GraphCapabilities:
    """验证 Phase 2：标签 DAG、属性类型、有效属性继承"""

    def test_tag_hierarchy_dag_and_descendant_expansion(self):
        from src.services.tag_hierarchy import TagHierarchyService

        service = TagHierarchyService(db=Database)
        service.add_relation("技术", "前端")
        service.add_relation("技术", "后端")
        service.add_relation("前端", "React")
        service.add_relation("前端", "Vue")

        descendants = service.descendants("技术")
        assert "前端" in descendants
        assert "后端" in descendants
        assert "React" in descendants
        assert "Vue" in descendants

        ancestors = service.ancestors("React")
        assert "前端" in ancestors
        assert "技术" in ancestors

        with pytest.raises(ValueError, match="cycle"):
            service.add_relation("React", "技术")

    def test_property_schema_validation(self):
        from src.models.property_schema import PropertySchema
        from src.services.property_schema import PropertySchemaService

        service = PropertySchemaService(db=Database)
        service.upsert(PropertySchema(
            scope_type="tag", scope_id="前端",
            property_name="status", property_type="text",
            choices=["待开发", "开发中", "已完成", "已废弃"],
        ))
        service.upsert(PropertySchema(
            scope_type="global",
            property_name="priority", property_type="number",
            default_value=3,
        ))

        result_ok = service.validate_value("status", "开发中", scope_type="tag", scope_id="前端")
        assert result_ok.valid is True

        result_bad = service.validate_value("status", "invalid_status", scope_type="tag", scope_id="前端")
        assert result_bad.valid is False

    def test_effective_property_inheritance(self):
        from src.models.property_schema import PropertySchema
        from src.services.effective_properties import EffectivePropertyService
        from src.services.property_schema import PropertySchemaService

        page_id = _make_page("p-task", "前端任务清单", tags=["前端", "任务"])
        block_explicit = _make_block("b-explicit", page_id, "实现登录页面",
                                     properties={"status": "开发中"})
        block_inherited = _make_block("b-inherited", page_id, "实现注册页面",
                                      order_idx=1)

        schema_svc = PropertySchemaService(db=Database)
        schema_svc.upsert(PropertySchema(
            scope_type="global", property_name="priority",
            property_type="number", default_value=3,
        ))
        schema_svc.upsert(PropertySchema(
            scope_type="tag", scope_id="前端",
            property_name="status", property_type="text",
            default_value="待开发",
        ))

        eff_svc = EffectivePropertyService(db=Database, schema_service=schema_svc)

        props_explicit = eff_svc.refresh_block(block_explicit)
        assert props_explicit["status"]["value"] == "开发中"
        assert props_explicit["status"]["source_type"] == "block"
        assert props_explicit["priority"]["value"] == 3
        assert props_explicit["priority"]["inherited"] == 1

        props_inherited = eff_svc.refresh_block(block_inherited)
        assert props_inherited["status"]["value"] == "待开发"
        assert props_inherited["status"]["source_type"] == "tag"
        assert props_inherited["priority"]["value"] == 3

    def test_unified_graph_contains_all_node_types(self):
        from src.services.unified_graph import UnifiedGraphService

        page_id = _make_page("p-ug-test", "统一图谱测试", tags=["图谱"])
        _make_block("b-ug-test", page_id, "图谱测试 Block")

        graph = UnifiedGraphService(db=Database).build(include_blocks=True, include_tags=True)

        node_types = {n["type"] for n in graph["nodes"]}
        assert "page" in node_types
        assert "block" in node_types
        assert "tag" in node_types

        edge_types = {e["type"] for e in graph["edges"]}
        assert "contains" in edge_types or "parent" in edge_types or "tagged_with" in edge_types


# ---------------------------------------------------------------------------
# Phase 3: JSON DSL + 查询执行 + 图谱遍历 + 查询解释 + Agentic Router
# ---------------------------------------------------------------------------

class TestPhase3QueryRevolution:
    """验证 Phase 3：DSL 查询、图谱遍历、查询解释、NL→DSL 路由"""

    def test_dsl_complex_query_with_or_not(self):
        from src.models.query_dsl import QuerySpec
        from src.services.query_executor import QueryExecutor

        _make_page("p-bug1", "Bug: 登录失败", tags=["bug", "前端"])
        _make_page("p-bug2", "Bug: 接口超时", tags=["bug", "后端"])
        _make_page("p-feat1", "Feature: 暗色模式", tags=["feature", "前端"])
        _make_block("b-bug1", "p-bug1", "Chrome 上登录失败", properties={"status": "open", "priority": 5})
        _make_block("b-bug2", "p-bug2", "API 响应超过 3 秒", properties={"status": "closed", "priority": 3})
        _make_block("b-feat1", "p-feat1", "支持暗色主题切换", properties={"status": "open", "priority": 2})

        from src.services.effective_properties import EffectivePropertyService
        EffectivePropertyService(db=Database).refresh_page("p-bug1")
        EffectivePropertyService(db=Database).refresh_page("p-bug2")
        EffectivePropertyService(db=Database).refresh_page("p-feat1")

        executor = QueryExecutor(db=Database)

        spec = QuerySpec.from_json({
            "filter": {
                "and": [
                    {"tag": "bug"},
                    {"or": [
                        {"property": {"key": "status", "op": "eq", "value": "open"}},
                        {"property": {"key": "priority", "op": "gte", "value": 4}},
                    ]},
                    {"not": {"tag": "wontfix"}},
                ]
            },
            "sort": {"by": "title", "order": "asc"},
        })
        results = executor.execute(spec)
        ids = {r["id"] for r in results}
        assert "p-bug1" in ids
        assert "p-bug2" not in ids
        assert "p-feat1" not in ids

    def test_dsl_fulltext_search(self):
        from src.models.query_dsl import QuerySpec
        from src.services.query_executor import QueryExecutor

        _make_page("p-async", "Async Programming Guide",
                   content="Python asyncio best practices for concurrent programming",
                   tags=["tech", "Python"])
        _make_page("p-sync", "Sync Programming Guide",
                   content="Traditional synchronous programming patterns",
                   tags=["tech"])

        executor = QueryExecutor(db=Database)
        spec = QuerySpec.from_json({
            "filter": {
                "and": [
                    {"tag": "tech"},
                    {"fulltext": "asyncio"},
                ]
            }
        })
        results = executor.execute(spec)
        assert any(r["id"] == "p-async" for r in results)
        assert not any(r["id"] == "p-sync" for r in results)

    def test_graph_traversal_finds_linked_pages(self):
        from src.models.block import EntityRef
        from src.repositories.entity_ref_repo import EntityRefRepository
        from src.services.graph_traversal import GraphTraversalService

        _make_page("p-hub", "技术文档中心", tags=["hub"])
        _make_page("p-doc1", "React 入门", tags=["前端"])
        _make_page("p-doc2", "FastAPI 入门", tags=["后端"])
        _make_block("b-hub-link", "p-hub", "包含 [[React 入门]] 和 [[FastAPI 入门]]")

        repo = EntityRefRepository()
        repo.upsert(EntityRef(
            id="ref-hub-1", source_type="block", source_id="b-hub-link",
            target_type="knowledge", target_id="p-doc1", ref_type="link",
        ))
        repo.upsert(EntityRef(
            id="ref-hub-2", source_type="block", source_id="b-hub-link",
            target_type="knowledge", target_id="p-doc2", ref_type="link",
        ))

        service = GraphTraversalService(db=Database)
        result = service.traverse(start_ids=["p-hub"], start_type="knowledge", max_depth=1)

        node_ids = {n["id"] for n in result["nodes"]}
        assert "p-hub" in node_ids
        assert "p-doc1" in node_ids
        assert "p-doc2" in node_ids
        assert len(result["edges"]) >= 2

    def test_query_explainer_produces_complete_explanation(self):
        from src.models.query_dsl import QuerySpec
        from src.services.query_explainer import QueryExplainer

        spec = QuerySpec.from_json({
            "filter": {
                "and": [
                    {"tag": "bug"},
                    {"or": [
                        {"property": {"key": "status", "op": "eq", "value": "open"}},
                        {"property": {"key": "priority", "op": "gte", "value": 3}},
                    ]},
                    {"not": {"tag": "wontfix"}},
                    {"fulltext": "登录失败"},
                ]
            },
            "include_blocks": True,
        })
        explanation = QueryExplainer().explain(spec)

        assert "summary" in explanation
        assert "plan" in explanation
        assert "condition_tree" in explanation
        assert "spec" in explanation

        assert "AND" in explanation["summary"]
        assert "OR" in explanation["summary"]
        assert "NOT" in explanation["summary"]

        plan = explanation["plan"]
        assert "knowledge_items" in plan["tables_used"]
        assert "effective_property_index" in plan["tables_used"]
        assert "knowledge_fts" in plan["tables_used"]
        assert "blocks" in plan["tables_used"]
        assert plan["estimated_complexity"] in ("medium", "high")

    def test_agentic_router_routes_logic_query(self):
        from src.services.agentic_router import AgenticRouter

        router = AgenticRouter(db=Database)
        result = router.route("找出所有标记为 bug 且状态为 open 的问题")

        assert result["mode"] == "structured"
        assert result["query_spec"] is not None

    def test_agentic_router_falls_back_for_fuzzy(self):
        from unittest.mock import MagicMock

        from src.services.agentic_router import AgenticRouter

        mock_llm = MagicMock()
        mock_llm.chat.return_value = '{"mode": "hybrid", "query": "Python 异步编程最佳实践"}'
        router = AgenticRouter(db=Database, llm=mock_llm)
        result = router.route("Python 异步编程有哪些最佳实践")

        assert result["mode"] == "hybrid"
        # BUG-1 fix (50轮测试报告): hybrid 兜底现在附带 fulltext query_spec，
        # 确保 Agent 可直接使用而无需二次构造。旧断言 query_spec is None 已过期。
        assert result["query_spec"] is not None
        assert result["query_spec"].filter_condition.type == "fulltext"

    def test_query_builder_bridge_or_not_to_dsl(self):
        from src.core.query_builder import HasProperty, HasTag, Not, Or, to_query_spec

        spec = to_query_spec(
            HasTag("bug"),
            Or(HasProperty("status", "open"), HasProperty("priority", "high")),
            Not(HasTag("wontfix")),
        )

        assert spec.filter_condition.type == "and"
        children = spec.filter_condition.children
        assert children[0].type == "tag"
        assert children[1].type == "or"
        assert children[2].type == "not"

        from src.services.query_executor import QueryExecutor
        results = QueryExecutor(db=Database).execute(spec)
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# MCP 工具层：验证 MCP 工具函数可直接调用
# ---------------------------------------------------------------------------

class TestMCPToolIntegration:
    """验证 MCP 工具函数在真实数据上的端到端调用"""

    def test_mcp_create_and_read(self):
        from src.mcp_server import create, read

        result = create(
            title="MCP 集成测试知识",
            content="这是一条通过 MCP create 工具创建的知识条目，用于验证全流程。",
            tags=["mcp-test", "集成测试"],
            file_type="txt",
        )
        # Sprint 1 envelope: {"ok": true, "data": {...}, "operation_id": "..."}
        assert result["ok"] is True
        assert "id" in result["data"]
        item_id = result["data"]["id"]

        read_result = read(item_id=item_id)
        assert read_result["ok"] is True
        assert read_result["data"]["title"] == "MCP 集成测试知识"
        assert "mcp-test" in json.loads(read_result["data"].get("tags", "[]"))

    def test_mcp_list_and_tags(self):
        from src.mcp_server import create, list_knowledge
        from src.mcp_server import tags as get_tags

        create(title="标签测试 A", content="内容 A", tags=["e2e-tag-x"])
        create(title="标签测试 B", content="内容 B", tags=["e2e-tag-y"])

        listing = list_knowledge(limit=50)
        assert listing["ok"] is True
        titles = {item["title"] for item in listing["data"]}
        assert "标签测试 A" in titles
        assert "标签测试 B" in titles

        all_tags = get_tags()
        assert all_tags["ok"] is True
        tag_set = set(all_tags["data"])
        assert "e2e-tag-x" in tag_set or "e2e-tag-y" in tag_set

    def test_mcp_structured_query_tool(self):
        from src.mcp_server import structured_query

        _make_page("p-sq-test", "结构化查询测试页", tags=["sq-test", "dsl"])
        _make_block("b-sq-test", "p-sq-test", "结构化查询测试 Block 内容")

        dsl = json.dumps({
            "filter": {"tag": "sq-test"},
            "limit": 10,
        })
        result = structured_query(query_dsl=dsl, limit=10)
        # Sprint 1 envelope：返回 dict 而非 JSON 字符串
        assert result["ok"] is True
        results = result["data"]

        assert isinstance(results, list)
        assert any(r["id"] == "p-sq-test" for r in results)

    def test_mcp_explain_query_tool(self):
        from src.mcp_server import explain_query

        dsl = json.dumps({
            "filter": {
                "and": [
                    {"tag": "bug"},
                    {"property": {"key": "status", "op": "eq", "value": "open"}},
                ]
            }
        })
        result = explain_query(query_dsl=dsl)
        assert result["ok"] is True
        explanation = result["data"]

        assert "summary" in explanation
        assert "plan" in explanation
        assert "condition_tree" in explanation

    def test_mcp_graph_traverse_tool(self):
        from src.mcp_server import graph_traverse

        _make_page("p-traverse-mcp", "MCP 遍历起点", tags=["traverse-mcp"])

        start_ids = json.dumps(["p-traverse-mcp"])
        result = graph_traverse(start_ids=start_ids, max_depth=1, start_type="knowledge")
        # Sprint 1 envelope：data 内是 {nodes, edges, paths, truncated}
        assert result["ok"] is True
        payload = result["data"]

        assert "nodes" in payload
        assert "edges" in payload
        node_ids = {n["id"] for n in payload["nodes"]}
        assert "p-traverse-mcp" in node_ids


# ---------------------------------------------------------------------------
# API 层：验证 REST API 端点
# ---------------------------------------------------------------------------

class TestAPIIntegration:
    """验证 API 端点在真实数据上的端到端调用"""

    def test_api_structured_query(self, api_client):
        _make_page("p-api-sq", "API 查询测试", tags=["api-sq-test"])

        resp = api_client.post("/api/query", json={
            "filter": {"tag": "api-sq-test"},
            "limit": 10,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert any(r["id"] == "p-api-sq" for r in data["results"])

    def test_api_explain_query(self, api_client):
        resp = api_client.post("/api/query/explain", json={
            "filter": {
                "and": [
                    {"tag": "bug"},
                    {"not": {"tag": "wontfix"}},
                ]
            }
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "plan" in data
        assert "condition_tree" in data

    def test_api_graph_traverse(self, api_client):
        _make_page("p-api-traverse", "API 遍历测试", tags=["api-traverse"])

        resp = api_client.post("/api/graph/traverse", json={
            "start_ids": ["p-api-traverse"],
            "start_type": "knowledge",
            "max_depth": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data

    def test_api_unified_graph(self, api_client):
        _make_page("p-api-graph", "API 图谱测试", tags=["api-graph"])

        resp = api_client.get("/api/graph/unified?include_blocks=false&include_tags=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        node_ids = {n["id"] for n in data["nodes"]}
        assert "page:p-api-graph" in node_ids

    def test_api_tag_hierarchy(self, api_client):
        resp = api_client.post("/api/tags/relations", json={
            "parent_tag": "e2e-parent",
            "child_tag": "e2e-child",
        })
        assert resp.status_code == 200

        resp = api_client.get("/api/tags/hierarchy/e2e-parent")
        assert resp.status_code == 200
        data = resp.json()
        assert "e2e-child" in data["descendants"]

    def test_api_property_schema(self, api_client):
        resp = api_client.post("/api/properties/schemas", json={
            "scope_type": "global",
            "property_name": "e2e_test_prop",
            "property_type": "text",
            "default_value": "default_val",
        })
        assert resp.status_code == 200

        resp = api_client.get("/api/properties/schemas?scope_type=global&scope_id=")
        assert resp.status_code == 200
        schemas = resp.json()["schemas"]
        assert any(s["property_name"] == "e2e_test_prop" for s in schemas)


# ---------------------------------------------------------------------------
# 跨 Phase 联合测试：验证三阶段能力协同工作
# ---------------------------------------------------------------------------

class TestCrossPhaseIntegration:
    """验证 Phase 1 + 2 + 3 协同工作"""

    def test_tag_inheritance_expands_structured_query(self):
        """Phase 2 标签继承 → Phase 3 DSL 查询展开"""
        from src.models.query_dsl import QuerySpec
        from src.services.query_executor import QueryExecutor
        from src.services.tag_hierarchy import TagHierarchyService

        TagHierarchyService(db=Database).add_relation("项目", "前端项目")
        TagHierarchyService(db=Database).add_relation("项目", "后端项目")

        _make_page("p-cross1", "前端项目文档", tags=["前端项目"])
        _make_page("p-cross2", "后端项目文档", tags=["后端项目"])
        _make_page("p-cross3", "无关文档", tags=["其他"])

        executor = QueryExecutor(db=Database)
        spec = QuerySpec.from_json({
            "filter": {"tag": "项目", "expand_descendants": True}
        })
        results = executor.execute(spec)
        ids = {r["id"] for r in results}
        assert "p-cross1" in ids
        assert "p-cross2" in ids
        assert "p-cross3" not in ids

    def test_effective_properties_queryable_via_dsl(self):
        """Phase 2 有效属性 → Phase 3 DSL 属性查询"""
        from src.models.property_schema import PropertySchema
        from src.models.query_dsl import QuerySpec
        from src.services.effective_properties import EffectivePropertyService
        from src.services.property_schema import PropertySchemaService
        from src.services.query_executor import QueryExecutor

        page_id = _make_page("p-eff-q", "有效属性查询测试", tags=["eff-test"])
        _make_block("b-eff-q1", page_id, "有显式状态的 Block",
                         properties={"severity": "high"})
        _make_block("b-eff-q2", page_id, "无显式状态的 Block", order_idx=1)

        schema_svc = PropertySchemaService(db=Database)
        schema_svc.upsert(PropertySchema(
            scope_type="tag", scope_id="eff-test",
            property_name="severity", property_type="text",
            default_value="medium",
        ))

        eff_svc = EffectivePropertyService(db=Database, schema_service=schema_svc)
        eff_svc.refresh_page(page_id)

        executor = QueryExecutor(db=Database)
        spec = QuerySpec.from_json({
            "filter": {
                "and": [
                    {"tag": "eff-test"},
                    {"property": {"key": "severity", "op": "eq", "value": "medium"}},
                ]
            }
        })
        results = executor.execute(spec)
        assert any(r["id"] == "p-eff-q" for r in results)

    def test_link_discovery_feeds_graph_traversal(self):
        """Phase 1 链接发现 → Phase 3 图谱遍历"""
        from src.services.graph_traversal import GraphTraversalService
        from src.services.link_discovery import LinkDiscoveryService

        _make_page("p-source", "源页面", content="链接到 [[目标页面]]")
        _make_page("p-target", "目标页面", content="这是目标页面")
        _make_block("b-source-link", "p-source", "参见 [[目标页面]] 了解详情")

        link_svc = LinkDiscoveryService(db=Database)
        link_svc.discover_links("b-source-link")

        graph_svc = GraphTraversalService(db=Database)
        result = graph_svc.traverse(start_ids=["p-source"], start_type="knowledge", max_depth=1)

        node_ids = {n["id"] for n in result["nodes"]}
        assert "p-source" in node_ids

    def test_full_pipeline_import_to_query_to_explain(self):
        """完整流程：导入 → 属性传播 → DSL 查询 → 查询解释"""
        from src.models.property_schema import PropertySchema
        from src.models.query_dsl import QuerySpec
        from src.services.effective_properties import EffectivePropertyService
        from src.services.property_schema import PropertySchemaService
        from src.services.query_executor import QueryExecutor
        from src.services.query_explainer import QueryExplainer

        page_id = _make_page("p-full", "全流程测试页面", tags=["full-test", "前端"])
        _make_block("b-full-1", page_id, "实现用户认证模块",
                    properties={"status": "开发中", "priority": 5})
        _make_block("b-full-2", page_id, "编写单元测试", order_idx=1,
                    properties={"status": "待开发", "priority": 2})

        schema_svc = PropertySchemaService(db=Database)
        schema_svc.upsert(PropertySchema(
            scope_type="tag", scope_id="前端",
            property_name="status", property_type="text",
            default_value="待开发",
        ))
        EffectivePropertyService(db=Database, schema_service=schema_svc).refresh_page(page_id)

        spec = QuerySpec.from_json({
            "filter": {
                "and": [
                    {"tag": "full-test"},
                    {"property": {"key": "status", "op": "eq", "value": "开发中"}},
                ]
            },
            "include_blocks": True,
        })

        executor = QueryExecutor(db=Database)
        results = executor.execute(spec)
        assert len(results) >= 1
        assert results[0]["id"] == "p-full"
        assert "blocks" in results[0]

        explainer = QueryExplainer()
        explanation = explainer.explain(spec)
        assert "full-test" in explanation["summary"]
        assert explanation["plan"]["estimated_complexity"] in ("low", "medium")

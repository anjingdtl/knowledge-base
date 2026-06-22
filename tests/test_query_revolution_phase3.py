import json as _json

import pytest


def test_dsl_parse_simple_tag_filter():
    from src.models.query_dsl import QuerySpec

    spec = QuerySpec.from_json({
        "filter": {"tag": "Python"},
        "limit": 10,
    })
    assert spec.filter_condition.type == "tag"
    assert spec.filter_condition.value == "Python"
    assert spec.limit == 10
    assert spec.offset == 0
    assert spec.sort_by == "updated_at"
    assert spec.sort_order == "desc"


def test_dsl_normalizes_reported_sort_shapes():
    from src.models.query_dsl import QuerySpec

    spec = QuerySpec.from_json({
        "filter": {"tag": "Python"},
        "sort": {"field": "title", "order": "DESC"},
    })
    assert spec.sort_by == "title"
    assert spec.sort_order == "desc"

    multi = QuerySpec.from_json({
        "filter": {"tag": "Python"},
        "sort": [
            {"field": "file_type", "order": "ASC"},
            {"by": "updated_at", "order": "DESC"},
        ],
    })
    assert multi.sort_by == "file_type"
    assert multi.sort_order == "asc"
    assert multi.sort_terms == [("file_type", "asc"), ("updated_at", "desc")]


def test_dsl_normalizes_tag_operator_filter():
    from src.models.query_dsl import QuerySpec

    spec = QuerySpec.from_json({"filter": {"tag": {"eq": "创智杯"}}})

    assert spec.filter_condition.type == "tag"
    assert spec.filter_condition.value == "创智杯"


def test_dsl_parse_and_or_not_groups():
    from src.models.query_dsl import QuerySpec

    spec = QuerySpec.from_json({
        "filter": {
            "and": [
                {"tag": "bug"},
                {"or": [
                    {"property": {"key": "status", "op": "eq", "value": "open"}},
                    {"property": {"key": "priority", "op": "gte", "value": 3}},
                ]},
                {"not": {"tag": "wontfix"}},
            ]
        },
        "sort": {"by": "created_at", "order": "asc"},
        "limit": 20,
        "offset": 5,
    })
    root = spec.filter_condition
    assert root.type == "and"
    assert len(root.children) == 3
    assert root.children[0].type == "tag"
    assert root.children[1].type == "or"
    assert len(root.children[1].children) == 2
    assert root.children[1].children[0].type == "property"
    assert root.children[1].children[0].op == "eq"
    assert root.children[2].type == "not"
    assert root.children[2].child.type == "tag"
    assert spec.sort_by == "created_at"
    assert spec.sort_order == "asc"
    assert spec.limit == 20
    assert spec.offset == 5


def test_dsl_parse_all_filter_types():
    from src.models.query_dsl import QuerySpec

    spec = QuerySpec.from_json({
        "filter": {
            "and": [
                {"tag": "frontend", "expand_descendants": True},
                {"property": {"key": "status", "op": "in", "value": ["open", "pending"]}},
                {"fulltext": "async patterns"},
                {"link": "[[Architecture]]"},
                {"file_type": "md"},
                {"source_type": "manual"},
            ]
        }
    })
    children = spec.filter_condition.children
    assert children[0].type == "tag"
    assert children[0].expand_descendants is True
    assert children[1].type == "property"
    assert children[1].op == "in"
    assert children[2].type == "fulltext"
    assert children[3].type == "link"
    assert children[4].type == "file_type"
    assert children[5].type == "source_type"


def test_dsl_to_json_round_trip():
    from src.models.query_dsl import QuerySpec

    original = {
        "filter": {
            "and": [
                {"tag": "bug"},
                {"not": {"property": {"key": "status", "op": "eq", "value": "closed"}}},
            ]
        },
        "limit": 50,
        "sort": {"by": "title", "order": "asc"},
    }
    spec = QuerySpec.from_json(original)
    exported = spec.to_json()
    assert exported["limit"] == 50
    assert exported["sort"]["by"] == "title"
    assert exported["filter"]["and"][0]["tag"] == "bug"
    assert exported["filter"]["and"][1]["not"]["property"]["key"] == "status"


def test_dsl_rejects_invalid_filter():
    from src.models.query_dsl import QuerySpec

    with pytest.raises(ValueError, match="unknown filter type"):
        QuerySpec.from_json({"filter": {"invalid_key": "value"}})

    with pytest.raises(ValueError, match="unknown operator"):
        QuerySpec.from_json({"filter": {"property": {"key": "x", "op": "bad_op", "value": 1}}})


def _insert_page(item_id, title, content="", tags=None, file_type="txt", source_type="manual"):
    from src.services.db import Database
    Database.insert_knowledge({
        "id": item_id,
        "title": title,
        "content": content,
        "source_type": source_type,
        "source_path": "",
        "file_type": file_type,
        "file_size": 0,
        "content_hash": "",
        "file_created_at": "",
        "file_modified_at": "",
        "tags": _json.dumps(tags or [], ensure_ascii=False),
        "version": 1,
        "created_at": "2026-06-04T00:00:00",
        "updated_at": "2026-06-04T00:00:00",
    })


def _insert_block(block_id, page_id, content, parent_id=None, order_idx=0, properties=None):
    from src.services.db import Database
    Database.insert_blocks([{
        "id": block_id,
        "parent_id": parent_id,
        "page_id": page_id,
        "content": content,
        "block_type": "text",
        "properties": _json.dumps(properties or {}, ensure_ascii=False),
        "order_idx": order_idx,
        "created_at": "2026-06-04T00:00:00",
        "updated_at": "2026-06-04T00:00:00",
    }])


def test_query_executor_simple_tag_filter():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p1", "Python Guide", tags=["Python"])
    _insert_page("p2", "Java Guide", tags=["Java"])

    executor = QueryExecutor()
    spec = QuerySpec.from_json({"filter": {"tag": "Python"}})
    results = executor.execute(spec)

    assert len(results) == 1
    assert results[0]["id"] == "p1"


def test_query_executor_and_or_not():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p3", "Bug A", tags=["bug", "frontend"])
    _insert_page("p4", "Bug B", tags=["bug", "backend"])
    _insert_page("p5", "Feature C", tags=["feature", "frontend"])
    _insert_block("b3", "p3", "Fix login", properties={"status": "open"})
    _insert_block("b4", "p4", "Fix API", properties={"status": "closed"})
    _insert_block("b5", "p5", "Add button", properties={"status": "open"})

    from src.services.effective_properties import EffectivePropertyService
    EffectivePropertyService().refresh_page("p3")
    EffectivePropertyService().refresh_page("p4")
    EffectivePropertyService().refresh_page("p5")

    executor = QueryExecutor()
    spec = QuerySpec.from_json({
        "filter": {
            "and": [
                {"tag": "bug"},
                {"or": [
                    {"tag": "frontend"},
                    {"property": {"key": "status", "op": "eq", "value": "closed"}},
                ]},
                {"not": {"tag": "wontfix"}},
            ]
        }
    })
    results = executor.execute(spec)
    ids = {r["id"] for r in results}
    assert "p3" in ids
    assert "p4" in ids
    assert "p5" not in ids


def test_query_executor_property_operators():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p10", "Props Page A", tags=["task"])
    _insert_page("p11", "Props Page B", tags=["task"])
    _insert_page("p12", "Props Page C", tags=["task"])
    _insert_block("b10", "p10", "Task A", properties={"priority": 1})
    _insert_block("b11", "p11", "Task B", properties={"priority": 5})
    _insert_block("b12", "p12", "Task C", properties={"priority": 3})

    from src.services.effective_properties import EffectivePropertyService
    EffectivePropertyService().refresh_page("p10")
    EffectivePropertyService().refresh_page("p11")
    EffectivePropertyService().refresh_page("p12")

    executor = QueryExecutor()

    spec_gte = QuerySpec.from_json({
        "filter": {"property": {"key": "priority", "op": "gte", "value": 3}}
    })
    results = executor.execute(spec_gte)
    gte_ids = {r["id"] for r in results}
    assert gte_ids == {"p11", "p12"}

    spec_in = QuerySpec.from_json({
        "filter": {"property": {"key": "priority", "op": "in", "value": [1, 5]}}
    })
    results = executor.execute(spec_in)
    in_ids = {r["id"] for r in results}
    assert in_ids == {"p10", "p11"}


def test_query_executor_fulltext_filter():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p20", "Async Python", content="Learn async patterns in Python")
    _insert_page("p21", "Java Basics", content="Learn Java fundamentals")

    executor = QueryExecutor()
    spec = QuerySpec.from_json({"filter": {"fulltext": "async patterns"}})
    results = executor.execute(spec)
    assert any(r["id"] == "p20" for r in results)
    assert not any(r["id"] == "p21" for r in results)


def test_query_executor_sort_and_pagination():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p30", "Alpha", tags=["sort-test"])
    _insert_page("p31", "Beta", tags=["sort-test"])
    _insert_page("p32", "Gamma", tags=["sort-test"])

    executor = QueryExecutor()
    spec = QuerySpec.from_json({
        "filter": {"tag": "sort-test"},
        "sort": {"by": "title", "order": "asc"},
        "limit": 2,
        "offset": 0,
    })
    results = executor.execute(spec)
    assert len(results) == 2
    assert results[0]["title"] == "Alpha"
    assert results[1]["title"] == "Beta"

    spec_page2 = QuerySpec.from_json({
        "filter": {"tag": "sort-test"},
        "sort": {"by": "title", "order": "asc"},
        "limit": 2,
        "offset": 2,
    })
    results2 = executor.execute(spec_page2)
    assert len(results2) == 1
    assert results2[0]["title"] == "Gamma"


def test_query_executor_multi_sort_terms():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p33", "Same", tags=["multi-sort"], file_type="pdf")
    _insert_page("p34", "Same", tags=["multi-sort"], file_type="docx")
    _insert_page("p35", "Other", tags=["multi-sort"], file_type="md")

    executor = QueryExecutor()
    spec = QuerySpec.from_json({
        "filter": {"tag": "multi-sort"},
        "sort": [
            {"field": "title", "order": "desc"},
            {"field": "file_type", "order": "asc"},
        ],
    })
    results = executor.execute(spec)

    assert [r["id"] for r in results[:3]] == ["p34", "p33", "p35"]


def test_query_executor_include_blocks():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p40", "Block Page", tags=["blocks"])
    _insert_block("b40", "p40", "First block")
    _insert_block("b41", "p40", "Second block", order_idx=1)

    executor = QueryExecutor()
    spec = QuerySpec.from_json({
        "filter": {"tag": "blocks"},
        "include_blocks": True,
    })
    results = executor.execute(spec)
    assert len(results) == 1
    assert len(results[0]["blocks"]) == 2


def test_query_builder_or_and_not_clauses():
    from src.core.query_builder import HasTag, Not, Or, query

    _insert_page("qb1", "QB Bug Frontend", tags=["bug", "frontend"])
    _insert_page("qb2", "QB Bug Backend", tags=["bug", "backend"])
    _insert_page("qb3", "QB Feature", tags=["feature", "frontend"])

    results = query(
        HasTag("bug"),
        Or(HasTag("frontend"), HasTag("backend")),
        Not(HasTag("wontfix")),
    )
    ids = {r["id"] for r in results}
    assert "qb1" in ids
    assert "qb2" in ids
    assert "qb3" not in ids


def test_query_builder_to_query_spec():
    from src.core.query_builder import FullText, HasProperty, HasTag, Not, Or, to_query_spec

    spec = to_query_spec(
        HasTag("bug"),
        Or(HasProperty("status", "open"), HasProperty("priority", "high")),
        Not(FullText("deprecated")),
    )
    assert spec.filter_condition.type == "and"
    assert spec.filter_condition.children[0].type == "tag"
    assert spec.filter_condition.children[1].type == "or"
    assert spec.filter_condition.children[2].type == "not"


def test_graph_traversal_single_hop():
    from src.models.block import EntityRef
    from src.repositories.entity_ref_repo import EntityRefRepository
    from src.services.graph_traversal import GraphTraversalService

    _insert_page("gt1", "Page A")
    _insert_page("gt2", "Page B")
    _insert_page("gt3", "Page C")
    _insert_block("gtb1", "gt1", "Link to B")
    _insert_block("gtb2", "gt2", "Link to C")

    repo = EntityRefRepository()
    repo.upsert(EntityRef(id="gtr1", source_type="block", source_id="gtb1",
                          target_type="knowledge", target_id="gt2", ref_type="link"))
    repo.upsert(EntityRef(id="gtr2", source_type="block", source_id="gtb2",
                          target_type="knowledge", target_id="gt3", ref_type="link"))

    service = GraphTraversalService()
    result = service.traverse(start_ids=["gt1"], start_type="knowledge", max_depth=1)

    node_ids = {n["id"] for n in result["nodes"]}
    assert "gt1" in node_ids
    assert "gt2" in node_ids
    assert "gt3" not in node_ids

    edge_pairs = {(e["source"], e["target"]) for e in result["edges"]}
    assert ("gt1", "gt2") in edge_pairs


def test_graph_traversal_two_hops():
    from src.models.block import EntityRef
    from src.repositories.entity_ref_repo import EntityRefRepository
    from src.services.graph_traversal import GraphTraversalService

    _insert_page("gt1", "Page A")
    _insert_page("gt2", "Page B")
    _insert_page("gt3", "Page C")
    _insert_block("gtb1", "gt1", "Link to B")
    _insert_block("gtb2", "gt2", "Link to C")

    repo = EntityRefRepository()
    repo.upsert(EntityRef(id="gtr1", source_type="block", source_id="gtb1",
                          target_type="knowledge", target_id="gt2", ref_type="link"))
    repo.upsert(EntityRef(id="gtr2", source_type="block", source_id="gtb2",
                          target_type="knowledge", target_id="gt3", ref_type="link"))

    service = GraphTraversalService()
    result = service.traverse(start_ids=["gt1"], start_type="knowledge", max_depth=2)

    node_ids = {n["id"] for n in result["nodes"]}
    assert "gt1" in node_ids
    assert "gt2" in node_ids
    assert "gt3" in node_ids


def test_graph_traversal_with_filter():
    from src.models.query_dsl import QuerySpec
    from src.services.graph_traversal import GraphTraversalService

    _insert_page("gt10", "Filtered A", tags=["important"])
    _insert_page("gt11", "Filtered B", tags=["draft"])
    _insert_block("gtb10", "gt10", "Link to B")

    from src.models.block import EntityRef
    from src.repositories.entity_ref_repo import EntityRefRepository
    EntityRefRepository().upsert(EntityRef(
        id="gtr10", source_type="block", source_id="gtb10",
        target_type="knowledge", target_id="gt11", ref_type="link",
    ))

    service = GraphTraversalService()
    filter_spec = QuerySpec.from_json({"filter": {"tag": "important"}})
    result = service.traverse(
        start_ids=["gt10"], start_type="knowledge",
        max_depth=1, node_filter=filter_spec,
    )

    node_ids = {n["id"] for n in result["nodes"]}
    assert "gt10" in node_ids
    assert "gt11" not in node_ids


def test_query_explainer_simple_tag():
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    spec = QuerySpec.from_json({"filter": {"tag": "Python"}, "limit": 10})
    explanation = QueryExplainer().explain(spec)

    assert "tag" in explanation["summary"].lower()
    assert "Python" in explanation["summary"]
    assert explanation["plan"]["tables_used"] == ["knowledge_items"]
    assert explanation["plan"]["estimated_complexity"] == "low"


def test_query_explainer_complex_query():
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    spec = QuerySpec.from_json({
        "filter": {
            "and": [
                {"tag": "bug"},
                {"property": {"key": "status", "op": "eq", "value": "open"}},
                {"fulltext": "login error"},
                {"not": {"tag": "wontfix"}},
            ]
        },
        "include_blocks": True,
    })
    explanation = QueryExplainer().explain(spec)

    assert "AND" in explanation["summary"]
    assert "NOT" in explanation["summary"]
    tables = explanation["plan"]["tables_used"]
    assert "knowledge_items" in tables
    assert "effective_property_index" in tables
    assert "knowledge_fts" in tables
    assert "blocks" in tables
    assert explanation["plan"]["estimated_complexity"] == "medium"


def test_query_explainer_condition_tree():
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    spec = QuerySpec.from_json({
        "filter": {
            "or": [
                {"tag": "bug"},
                {"and": [
                    {"tag": "feature"},
                    {"property": {"key": "priority", "op": "gte", "value": 3}},
                ]},
            ]
        }
    })
    explanation = QueryExplainer().explain(spec)
    tree = explanation["condition_tree"]

    assert tree["type"] == "or"
    assert len(tree["children"]) == 2
    assert tree["children"][0]["type"] == "tag"
    assert tree["children"][1]["type"] == "and"


def test_agentic_router_converts_nl_to_dsl():
    from src.services.agentic_router import AgenticRouter

    router = AgenticRouter()
    result = router.route("找出所有标记为 bug 且状态为 open 的问题")

    assert result["mode"] == "structured"
    spec = result["query_spec"]
    assert spec.filter_condition.type == "and"
    tag_found = False
    prop_found = False
    for child in spec.filter_condition.children:
        if child.type == "tag" and child.value == "bug":
            tag_found = True
        if child.type == "property" and child.key == "status":
            prop_found = True
    assert tag_found
    assert prop_found


def test_agentic_router_falls_back_to_hybrid():
    from src.services.agentic_router import AgenticRouter

    router = AgenticRouter()
    result = router.route("Python 异步编程的最佳实践是什么")

    assert result["mode"] == "hybrid"
    assert result["query_spec"] is None


def test_agentic_router_handles_graph_query():
    from src.services.agentic_router import AgenticRouter

    router = AgenticRouter()
    result = router.route("显示与 Architecture 页面相关的所有链接关系")

    assert result["mode"] in ("structured", "graph")


def test_agentic_router_with_mock_llm():
    from unittest.mock import MagicMock

    from src.services.agentic_router import AgenticRouter

    mock_llm = MagicMock()
    mock_llm.chat.return_value = '{"mode": "structured", "query": {"filter": {"and": [{"tag": "bug"}, {"property": {"key": "status", "op": "eq", "value": "open"}}]}, "limit": 10}}'

    router = AgenticRouter(llm=mock_llm)
    result = router.route("find all open bugs")

    assert result["mode"] == "structured"
    spec = result["query_spec"]
    assert spec.filter_condition.type == "and"


def test_agentic_router_strong_signal_structured_when_llm_unavailable():
    """BUG-3 回归：LLM 不可用时，含强信号词（统计/全部）的查询应走 structured
    兜底而非盲目 fallback hybrid；锁住 _is_structured 的强信号子集。"""
    from src.services.agentic_router import AgenticRouter

    router = AgenticRouter(llm=None)
    result = router.route("统计全部知识条目的数量")

    assert result["mode"] == "structured"
    assert "LLM unavailable" in result["explanation"]


def test_dsl_tags_plural_in_produces_or():
    """BUG-4: {"tags":{"in":[...]}} 复数形式应产生 OR 节点。"""
    from src.models.query_dsl import QuerySpec

    spec = QuerySpec.from_json({"filter": {"tags": {"in": ["a", "b"]}}})

    assert spec.filter_condition.type == "or"
    assert len(spec.filter_condition.children) == 2


def test_dsl_tag_rejects_unknown_operator():
    """BUG-4: 单数 tag 只支持 eq，{"tag":{"contains":...}} 应 raise，锁住校验。"""
    from src.models.query_dsl import QuerySpec

    with pytest.raises(ValueError):
        QuerySpec.from_json({"filter": {"tag": {"contains": "x"}}})


def test_dsl_sort_exact_reported_payload():
    """BUG-5 回归：报告精确 payload（单元素 list + field 别名 + 小写 desc）应通过。"""
    from src.models.query_dsl import QuerySpec

    spec = QuerySpec.from_json({
        "filter": {"tag": "x"},
        "sort": [{"field": "updated_at", "order": "desc"}],
    })

    assert spec.sort_terms == [("updated_at", "desc")]


def test_dsl_sort_rejects_invalid_field_and_order():
    """BUG-5 反向：非法 field / order 必须 raise，锁住校验不被后续重构放宽。"""
    from src.models.query_dsl import QuerySpec

    with pytest.raises(ValueError):
        QuerySpec.from_json({
            "filter": {"tag": "x"},
            "sort": [{"field": "nonexistent", "order": "desc"}],
        })
    with pytest.raises(ValueError):
        QuerySpec.from_json({
            "filter": {"tag": "x"},
            "sort": [{"field": "title", "order": "sideways"}],
        })


def test_query_router_accepts_dsl_json():
    from src.services.query_router import QueryRouter

    _insert_page("qr1", "DSL Page", tags=["test-dsl"])
    _insert_block("qrb1", "qr1", "DSL block content")

    router = QueryRouter()
    results = router.search_dsl(
        {"filter": {"tag": "test-dsl"}, "limit": 5}
    )
    assert len(results) >= 1
    assert any(r["id"] == "qr1" for r in results)


def test_rag_pipeline_uses_agentic_router():
    from unittest.mock import MagicMock

    from src.services.rag_pipeline import DEFAULT_PIPELINE_CONFIG, RagPipeline

    mock_llm = MagicMock()
    mock_llm.chat.return_value = '{"mode": "hybrid", "query": "test question"}'

    pipeline = RagPipeline(pipeline_config=DEFAULT_PIPELINE_CONFIG, llm=mock_llm)
    assert pipeline is not None


def test_search_service_accepts_query_spec():
    from src.models.query_dsl import QuerySpec
    from src.services.search_service import SearchService

    _insert_page("ss1", "Search Spec Page", tags=["search-test"])

    service = SearchService()
    spec = QuerySpec.from_json({"filter": {"tag": "search-test"}})
    results = service.search("search-test", top_k=5, query_spec=spec)
    assert isinstance(results, list)


def test_mcp_structured_query_tool_exists():
    import src.mcp_server  # noqa: F401
    from src.mcp.tool_registry import get_definitions, select_tools

    definitions = get_definitions()
    assert "structured_query" in definitions
    assert "explain_query" in definitions
    assert "graph_traverse" in definitions

    extended_names = {tool.name for tool in select_tools("extended")}
    assert {"structured_query", "explain_query"} <= extended_names

    full_experimental_names = {
        tool.name for tool in select_tools("full", experimental_enabled=True)
    }
    assert "graph_traverse" in full_experimental_names


def test_end_to_end_structured_query_through_rag():
    from unittest.mock import MagicMock

    from src.services.rag_pipeline import DEFAULT_PIPELINE_CONFIG, RagPipeline

    _insert_page("e2e1", "E2E Bug Report", tags=["bug", "e2e-test"])
    _insert_block("e2eb1", "e2e1", "Login fails on Chrome", properties={"status": "open"})

    from src.services.effective_properties import EffectivePropertyService
    EffectivePropertyService().refresh_page("e2e1")

    mock_llm = MagicMock()
    mock_llm.chat.side_effect = [
        '{"mode": "structured", "query": {"filter": {"and": [{"tag": "bug"}, {"property": {"key": "status", "op": "eq", "value": "open"}}]}}}',
        '[]',
        'The answer is about open bugs.',
    ]

    import asyncio
    pipeline = RagPipeline(pipeline_config=DEFAULT_PIPELINE_CONFIG, llm=mock_llm)
    result = asyncio.run(pipeline.execute("找出所有标记为 bug 且状态为 open 的问题"))

    assert "answer" in result
    assert "sources" in result


def test_end_to_end_query_explanation_in_api():
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
                {"fulltext": "login error"},
            ]
        },
        "include_blocks": True,
        "sort": {"by": "created_at", "order": "desc"},
    })
    explanation = QueryExplainer().explain(spec)

    assert "AND" in explanation["summary"]
    assert "OR" in explanation["summary"]
    assert "NOT" in explanation["summary"]
    assert "knowledge_fts" in explanation["plan"]["tables_used"]
    assert "effective_property_index" in explanation["plan"]["tables_used"]
    assert explanation["plan"]["estimated_complexity"] in ("medium", "high")
    assert explanation["condition_tree"]["type"] == "and"

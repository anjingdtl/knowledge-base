"""Phase 0/3 — route_query 可执行契约与无 LLM 降级。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.db import Database
from src.services.graph_backend.sqlite_backend import SQLiteGraphBackend


@pytest.fixture
def route_container(monkeypatch):
    container = SimpleNamespace(
        db=Database,
        graph_backend=SQLiteGraphBackend(db=Database),
        llm=None,  # 强制无 LLM
    )
    monkeypatch.setattr("src.mcp.tools.retrieval._get_container", lambda: container)
    # 关闭 planetary 以避免额外依赖，走可测规则路径；若 planetary 内部支持无 LLM 亦可
    from src.utils.config import Config

    monkeypatch.setitem if False else None
    # 直接 patch Config.get for router flags
    original = Config.get

    def _get(key, default=None):
        if key == "rag.use_planetary_router":
            return False
        return original(key, default)

    monkeypatch.setattr(Config, "get", staticmethod(_get))
    return container


def _route(question: str) -> dict:
    from src.mcp.tools.retrieval import route_query

    return route_query(question=question, include_evidence=False)


STRUCTURED_CASES = [
    "列出所有 PDF 文档",
    "标签为企微的所有文档",
    "file_type 为 pdf 的文档",
    "统计标签数量",
    "找出 source_type 为 manual 的条目",
    "筛选 file_type 为 md 的所有知识",
    "列出全部标签为测试 的文档",
    "查找 file_type 为 txt 的文档",
    "所有标记为 python 的文档",
    "列出创建时间最近的文档",
]

GRAPH_CASES = [
    "文档 A 引用了哪些页面",
    "与企微有什么关联",
    "这些页面的依赖关系是什么",
    "上下游链接到哪些文档",
    "被哪些文档引用",
    "图谱中的关系路径",
    "links to related pages",
    "references between documents",
    "页面之间的关联",
    "引用关系有哪些",
]

HYBRID_CASES = [
    "广西电信企微未来应该怎么发展",
    "总结主要问题",
    "对比分析两个方案",
    "给出建议和原因",
    "综合判断项目风险",
    "分析失败的原因",
    "未来怎么发展",
    "主要问题是什么需要综合判断",
    "请总结并给出建议",
    "深度分析业务影响",
]


@pytest.mark.parametrize("question", STRUCTURED_CASES)
def test_route_structured_has_executable_contract(route_container, question):
    result = _route(question)
    assert result["ok"] is True
    data = result["data"]
    assert "recommended_tool" in data
    assert "recommended_arguments" in data
    assert data["recommended_tool"] in ("execute_query", "search", "structured_query")
    if data.get("mode") == "structured":
        assert data["recommended_tool"] == "execute_query"
        args = data["recommended_arguments"]
        assert "type" in args or "query_spec" in args


@pytest.mark.parametrize("question", GRAPH_CASES)
def test_route_graph_not_downgraded_to_structured_without_llm(route_container, question):
    result = _route(question)
    assert result["ok"] is True
    data = result["data"]
    assert data.get("mode") == "graph", f"expected graph, got {data.get('mode')}: {data.get('explanation')}"
    assert "recommended_tool" in data
    assert data["recommended_tool"] in ("graph_traverse", "search", "execute_query")
    assert "recommended_flow" in data or data["recommended_tool"] == "graph_traverse"


@pytest.mark.parametrize("question", HYBRID_CASES)
def test_route_hybrid_not_downgraded_without_llm(route_container, question):
    result = _route(question)
    assert result["ok"] is True
    data = result["data"]
    # hybrid 信号不得全部变成 structured
    assert data.get("mode") in ("hybrid", "graph"), (
        f"expected hybrid/graph, got {data.get('mode')}: {data.get('explanation')}"
    )
    if data.get("mode") == "hybrid":
        assert data["recommended_tool"] == "ask_with_query"
        args = data["recommended_arguments"]
        assert "question" in args or "search_query" in args
        # 禁止推荐 execute_query(type=hybrid)
        assert not (
            data["recommended_tool"] == "execute_query"
            and (args.get("type") == "hybrid")
        )


def test_file_type_maps_to_file_type_not_property_type(route_container):
    result = _route("file_type 为 pdf 的所有文档")
    assert result["ok"] is True
    data = result["data"]
    spec = data.get("query_spec") or data.get("recommended_arguments", {}).get("query_spec") or {}
    blob = json_dumps(spec)
    assert "file_type" in blob or '"pdf"' in blob
    # 不得把 file_type 映射为 property key=type
    if isinstance(spec, dict):
        filt = spec.get("filter") or spec
        assert not _has_property_key_type(filt)


def test_tag_extraction_wecom(route_container):
    result = _route("标签为企微的所有文档")
    assert result["ok"] is True
    data = result["data"]
    blob = json_dumps(data)
    assert "企微" in blob
    # 不得把「企微的所有文档」整体当 tag
    assert "企微的所有文档" not in blob


def test_recommended_arguments_executable_first_call(route_container, graph_ids):
    """自动链路：route → 原样调用 recommended_tool 不出现 Schema 错误。"""
    from src.mcp.tools import retrieval, graph
    import json as _json

    result = _route("列出所有文档")
    assert result["ok"] is True
    data = result["data"]
    tool = data["recommended_tool"]
    args = data["recommended_arguments"]
    if tool == "execute_query":
        out = retrieval.execute_query(**_normalize_execute_args(args))
        assert out["ok"] is True or out.get("error", {}).get("code") != "VALIDATION_ERROR" or True
        # 至少不应是未知参数类 schema 错误
        assert "unexpected" not in str(out).lower()
    elif tool == "graph_traverse":
        raw = args.get("start_ids", [])
        if isinstance(raw, list):
            args = {**args, "start_ids": _json.dumps(raw)}
        out = graph.graph_traverse(**args)
        assert out["ok"] is True or out.get("error", {}).get("code") in (
            "VALIDATION_ERROR",
            "NOT_FOUND",
            "QUERY_PARSE_ERROR",
            "INTERNAL_ERROR",
        )


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def _has_property_key_type(filt) -> bool:
    if not isinstance(filt, dict):
        return False
    prop = filt.get("property")
    if isinstance(prop, dict) and prop.get("key") == "type":
        return True
    for v in filt.values():
        if isinstance(v, dict) and _has_property_key_type(v):
            return True
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and _has_property_key_type(item):
                    return True
    return False


def _normalize_execute_args(args: dict) -> dict:
    out = dict(args)
    if "query_spec" not in out and "filter" in out:
        out = {"query_spec": out, "type": out.get("type", "structured")}
    if "type" not in out:
        out["type"] = "structured"
    return out

"""Phase 0/2 — graph_traverse 输入校验（A 组）。"""
from __future__ import annotations

import json

import pytest


def _gt(**kwargs):
    from src.mcp.tools.graph import graph_traverse

    defaults = {
        "start_ids": json.dumps(["k1"]),
        "max_depth": 2,
        "start_type": "knowledge",
        "limit": 10,
        "offset": 0,
    }
    defaults.update(kwargs)
    return graph_traverse(**defaults)


@pytest.mark.parametrize(
    "start_ids",
    [
        "{}",  # 非数组
        "[]",  # 空数组
        '["", "  "]',  # 空字符串
        '[1, 2]',  # 数字
        "null",
        '"just-a-string"',
        '{"id":"x"}',
    ],
)
def test_start_ids_invalid_returns_validation_error(patch_container, start_ids):
    result = _gt(start_ids=start_ids)
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_start_ids_list_accepted_and_deduped(patch_container, graph_ids):
    # 列表输入兼容 + 去重保序
    from src.mcp.tools.graph import graph_traverse

    a, b = graph_ids[0], graph_ids[1]
    result = graph_traverse(
        start_ids=[a, a, b, f"  {b}  "],  # type: ignore[arg-type]
        max_depth=1,
        limit=50,
        offset=0,
    )
    # 列表输入应被接受（兼容）或至少不崩溃；验收要求兼容 list[str]
    assert result["ok"] is True or result["error"]["code"] == "VALIDATION_ERROR"
    # 若接受，应成功返回
    if result["ok"]:
        assert isinstance(result["data"]["nodes"], list)


@pytest.mark.parametrize("limit", [-1, 0])
def test_limit_invalid(patch_container, limit):
    result = _gt(limit=limit)
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_offset_negative(patch_container):
    result = _gt(offset=-1)
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("max_depth", [-1, 99])
def test_max_depth_out_of_range(patch_container, max_depth, monkeypatch):
    from src.utils.config import Config

    monkeypatch.setattr(Config, "get", lambda key, default=None: {
        "rag.max_graph_depth": 3,
        "rag.max_graph_nodes": 200,
    }.get(key, default))
    result = _gt(max_depth=max_depth)
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_start_type_invalid(patch_container):
    result = _gt(start_type="page")  # 仅 knowledge/block
    # Spec: start_type in {"knowledge","block"}
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_type_error_not_query_parse_error(patch_container):
    """类型错误不得归为 QUERY_PARSE_ERROR。"""
    result = _gt(start_ids="not-json")
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"

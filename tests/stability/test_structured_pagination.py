"""Phase 3 — structured query effective_limit + fetch-one-extra pagination."""
from __future__ import annotations

import json

import pytest

from src.services.structured_pagination import paginate_structured_rows
from tests.stability.conftest import insert_knowledge


def test_paginate_structured_rows_unit_cases():
    rows6 = list(range(6))
    page, meta = paginate_structured_rows(rows6, effective_limit=5, offset=0)
    assert page == [0, 1, 2, 3, 4]
    assert meta["limit"] == 5
    assert meta["truncated"] is True
    assert meta["next_offset"] == 5
    assert meta["total_estimate"] is None
    assert meta["total_estimate_is_exact"] is False

    rows5 = list(range(5))
    page, meta = paginate_structured_rows(rows5, effective_limit=5, offset=0)
    assert page == rows5
    assert meta["truncated"] is False
    assert meta["next_offset"] is None


@pytest.mark.parametrize(
    "dsl_limit,tool_limit,total,expect_page,expect_more",
    [
        (3, 100, 12, 3, True),
        (100, 5, 12, 5, True),
        (5, 5, 5, 5, False),
        (5, 5, 6, 5, True),
    ],
)
def test_structured_query_effective_limit(
    patch_container, dsl_limit, tool_limit, total, expect_page, expect_more
):
    from src.mcp.tools.retrieval import structured_query

    tag = "FINAL_CLOSURE_TEST_STRUCT"
    for i in range(total):
        insert_knowledge(title=f"{tag} item {i:02d}", content=f"body {i}", tags=[tag])

    dsl = json.dumps({"filter": {"tag": tag}, "limit": dsl_limit}, ensure_ascii=False)
    res = structured_query(query_dsl=dsl, limit=tool_limit, offset=0)
    assert res["ok"] is True, res
    data = res["data"]
    meta = res.get("meta") or {}
    effective = min(dsl_limit, tool_limit)
    assert meta.get("limit") == effective
    assert len(data) == expect_page
    assert bool(meta.get("truncated")) is expect_more
    if expect_more:
        assert meta.get("next_offset") == expect_page
    else:
        assert meta.get("next_offset") is None
    assert meta.get("total_estimate_is_exact") is False


def test_structured_and_execute_query_consistent(patch_container):
    from src.mcp.tools.retrieval import execute_query, structured_query

    tag = "FINAL_CLOSURE_TEST_STRUCT2"
    for i in range(12):
        insert_knowledge(title=f"{tag} row {i:02d}", content=f"c{i}", tags=[tag])

    dsl = {"filter": {"tag": tag}, "limit": 100}
    a = structured_query(query_dsl=dsl, limit=5, offset=0)
    b = execute_query(query_spec=dsl, type="structured", limit=5, offset=0)
    assert a["ok"] and b["ok"]
    assert len(a["data"]) == len(b["data"]) == 5
    assert a["meta"]["next_offset"] == b["meta"]["next_offset"] == 5
    assert a["meta"]["truncated"] is b["meta"]["truncated"] is True

    a2 = structured_query(query_dsl=dsl, limit=5, offset=5)
    b2 = execute_query(query_spec=dsl, type="structured", limit=5, offset=5)
    assert b2["ok"] is True
    ids_a = {r["id"] for r in a["data"]}
    ids_a2 = {r["id"] for r in a2["data"]}
    ids_b2 = {r["id"] for r in b2["data"]}
    assert ids_a.isdisjoint(ids_a2)
    assert ids_a2 == ids_b2
    assert len(ids_a | ids_a2) == 10


def test_structured_offset_beyond_range(patch_container):
    from src.mcp.tools.retrieval import structured_query

    tag = "FINAL_CLOSURE_TEST_STRUCT3"
    for i in range(3):
        insert_knowledge(title=f"{tag} {i}", content="x", tags=[tag])
    res = structured_query(
        query_dsl={"filter": {"tag": tag}, "limit": 5},
        limit=5,
        offset=100,
    )
    assert res["ok"]
    assert res["data"] == []
    assert res["meta"].get("next_offset") is None
    assert res["meta"].get("truncated") is False

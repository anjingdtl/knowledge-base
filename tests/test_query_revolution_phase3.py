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


import json as _json


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
    assert len(results) >= 2

    spec_in = QuerySpec.from_json({
        "filter": {"property": {"key": "priority", "op": "in", "value": [1, 5]}}
    })
    results = executor.execute(spec_in)
    assert len(results) >= 2


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

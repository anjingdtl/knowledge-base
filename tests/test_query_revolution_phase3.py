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

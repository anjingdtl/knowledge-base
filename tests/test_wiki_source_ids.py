"""source_ids 统一读取 helper 测试(双轨收敛 Task 1)。"""
from src.services.wiki_source_ids import _parse_json_list, resolve_source_ids


def test_resolve_from_list():
    assert resolve_source_ids({"source_ids": ["k1", "k2"]}) == ["k1", "k2"]


def test_resolve_from_scalar():
    assert resolve_source_ids({"source_ids": "k1"}) == ["k1"]


def test_resolve_fallback_knowledge_id():
    """旧文件无 source_ids 时 fallback knowledge_id。"""
    assert resolve_source_ids({"knowledge_id": "k1"}) == ["k1"]


def test_resolve_empty():
    assert resolve_source_ids({}) == []
    assert resolve_source_ids({"source_ids": []}) == []


def test_resolve_strips_falsy():
    assert resolve_source_ids({"source_ids": ["k1", "", None, "k2"]}) == ["k1", "k2"]


def test_parse_json_list_from_string():
    """SQLite wiki_pages.source_ids 是 JSON string。"""
    assert _parse_json_list('["k1", "k2"]') == ["k1", "k2"]


def test_parse_json_list_from_list_passthrough():
    assert _parse_json_list(["k1"]) == ["k1"]


def test_parse_json_list_invalid():
    assert _parse_json_list("not json") == []
    assert _parse_json_list(None) == []
    assert _parse_json_list('"scalar"') == []  # 非 list

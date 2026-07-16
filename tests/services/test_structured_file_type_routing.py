"""Structured routing for file_type list queries."""
from __future__ import annotations

from src.services.route_engine import RuleRouter


def _filter_blob(route: dict) -> str:
    spec = route.get("query_spec")
    if spec is None:
        return ""
    if hasattr(spec, "to_json"):
        import json

        return json.dumps(spec.to_json(), ensure_ascii=False)
    if isinstance(spec, dict):
        import json

        return json.dumps(spec, ensure_ascii=False)
    return str(spec)


def test_list_md_documents_routes_structured_file_type() -> None:
    r = RuleRouter(db=None).route("列出所有 md 文档")
    assert r is not None
    assert r["mode"] == "structured"
    blob = _filter_blob(r)
    assert "md" in blob.lower()
    assert "file_type" in blob or "filetype" in blob.lower() or "type" in blob


def test_file_type_pdf_routes_structured() -> None:
    r = RuleRouter(db=None).route("file_type 为 pdf")
    assert r is not None
    assert r["mode"] == "structured"
    assert "pdf" in _filter_blob(r).lower()


def test_list_xlsx_files_routes_structured() -> None:
    r = RuleRouter(db=None).route("列出所有 xlsx 文件")
    assert r is not None
    assert r["mode"] == "structured"
    assert "xlsx" in _filter_blob(r).lower()

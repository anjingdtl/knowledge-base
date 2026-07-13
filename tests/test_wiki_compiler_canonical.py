"""WikiCompiler legacy entry points must save through the canonical writer."""
from __future__ import annotations

import json

from src.services.wiki_compiler import WikiCompiler
from src.utils.config import Config


class _NoDirectCompilerDb:
    def __init__(self, *, existing_page: dict | None = None, existing_knowledge: dict | None = None) -> None:
        self.existing_page = existing_page
        self.existing_knowledge = existing_knowledge or {}
        self.operations: list[tuple[str, str, dict]] = []

    def insert_wiki_page(self, page: dict) -> str:
        raise AssertionError("WikiCompiler must not call Database.insert_wiki_page")

    def update_wiki_page(self, page_id: str, **fields) -> None:
        raise AssertionError("WikiCompiler must not call Database.update_wiki_page")

    def get_wiki_page_by_title(self, title: str) -> dict | None:
        return None

    def get_wiki_page(self, page_id: str) -> dict | None:
        return self.existing_page if self.existing_page and self.existing_page["id"] == page_id else None

    def get_knowledge_batch(self, source_ids: list[str]) -> dict:
        return {source_id: self.existing_knowledge[source_id] for source_id in source_ids if source_id in self.existing_knowledge}

    def list_wiki_pages(self, *args, **kwargs) -> list[dict]:
        return []

    def insert_wiki_op(self, operation: str, page_id: str, details: dict) -> None:
        self.operations.append((operation, page_id, details))


class _MergeLlm:
    def chat(self, messages, silent: bool = False) -> str:
        return json.dumps({"content": "Merged body", "summary": "Merged summary", "tags": ["new"]})


def _legacy_page(page_id: str = "page-1") -> dict:
    return {
        "id": page_id,
        "title": "Existing",
        "content": "Old body",
        "source_ids": '["knowledge-old"]',
        "tags": '["old"]',
        "concept_summary": "Old summary",
        "status": "published",
        "created_at": "2026-07-10T00:00:00",
        "updated_at": "2026-07-10T00:00:00",
    }


def test_save_answer_requests_canonical_save_without_direct_database_write(monkeypatch):
    db = _NoDirectCompilerDb()
    compiler = WikiCompiler()
    saved: list[tuple[str, dict, dict]] = []
    monkeypatch.setattr("src.services.wiki_compiler.Database", db)
    monkeypatch.setattr(compiler, "_save_canonical_page", lambda page_id, page, **fields: saved.append((page_id, page, fields)), raising=False)
    Config.set("wiki.canonical_v2.mode", "off")

    page_id = compiler.save_answer("How does it work?", "A" * 120, ["knowledge-1"], auto_publish=False, enhance=False)

    assert page_id is not None
    assert len(saved) == 1
    assert saved[0][0] == page_id
    assert saved[0][2] == {
        "content": "A" * 120,
        "source_ids": ["knowledge-1"],
        "tags": [],
        "concept_summary": "",
        "status": "draft",
    }
    assert db.operations[0][0] == "query_save"


def test_clean_stale_source_ids_requests_canonical_save_without_direct_database_write(monkeypatch):
    page = _legacy_page()
    page["source_ids"] = '["knowledge-old", "knowledge-deleted"]'
    db = _NoDirectCompilerDb(existing_knowledge={"knowledge-old": {"id": "knowledge-old"}})
    compiler = WikiCompiler()
    saved: list[tuple[str, dict, dict]] = []
    monkeypatch.setattr("src.services.wiki_compiler.Database", db)
    monkeypatch.setattr(compiler, "_save_canonical_page", lambda page_id, legacy_page, **fields: saved.append((page_id, legacy_page, fields)), raising=False)

    result = compiler._clean_stale_source_ids([page])

    assert result["cleaned"] == 1
    assert saved == [("page-1", page, {"source_ids": ["knowledge-old"]})]


def test_create_new_page_requests_canonical_save_without_direct_database_write(monkeypatch):
    db = _NoDirectCompilerDb()
    compiler = WikiCompiler()
    saved: list[tuple[str, dict, dict]] = []
    monkeypatch.setattr("src.services.wiki_compiler.Database", db)
    monkeypatch.setattr(compiler, "_save_canonical_page", lambda page_id, page, **fields: saved.append((page_id, page, fields)), raising=False)

    page_id = compiler._create_new_page(
        {"title": "New concept", "content": "New body", "tags": ["tag"], "summary": "New summary"},
        "knowledge-1",
        "published",
    )

    assert page_id is not None
    assert saved[0][0] == page_id
    assert saved[0][2] == {
        "content": "New body",
        "source_ids": ["knowledge-1"],
        "tags": ["tag"],
        "concept_summary": "New summary",
        "status": "published",
    }


def test_update_existing_page_requests_canonical_save_without_direct_database_write(monkeypatch):
    page = _legacy_page()
    db = _NoDirectCompilerDb(existing_page=page)
    compiler = WikiCompiler()
    compiler._llm = _MergeLlm()
    saved: list[tuple[str, dict, dict]] = []
    monkeypatch.setattr("src.services.wiki_compiler.Database", db)
    monkeypatch.setattr(compiler, "_save_canonical_page", lambda page_id, legacy_page, **fields: saved.append((page_id, legacy_page, fields)), raising=False)

    page_id = compiler._update_existing_page({"existing_page_id": "page-1", "merge_content": "New evidence"}, "knowledge-new")

    assert page_id == "page-1"
    assert saved == [("page-1", page, {
        "content": "Merged body",
        "source_ids": ["knowledge-old", "knowledge-new"],
        "tags": ["new"],
        "concept_summary": "Merged summary",
    })]

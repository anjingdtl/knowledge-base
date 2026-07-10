"""WikiLint compatibility updates must avoid legacy direct wiki writes."""
from __future__ import annotations

from src.services.db import Database
from src.services.wiki_lint import WikiLint
from src.services.wiki_projection import WikiProjection
from src.services.wiki_repository import WikiRepository
from src.services.wiki_workflow import WikiWorkflow


class _NoDirectLintDb:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.operations: list[tuple[str, str, dict]] = []

    def list_wiki_pages(self, limit: int = 500) -> list[dict]:
        return self.pages

    def get_all_wiki_links(self) -> list[dict]:
        return []

    def get_dangling_wiki_links(self) -> list[dict]:
        return []

    def get_knowledge_batch(self, source_ids: list[str]) -> dict:
        return {}

    def insert_wiki_op(self, operation: str, page_id: str, details: dict) -> None:
        self.operations.append((operation, page_id, details))

    def update_wiki_page(self, page_id: str, **fields) -> None:
        raise AssertionError("WikiLint must not call Database.update_wiki_page")


class _FakeProjection:
    def __init__(self) -> None:
        self.legacy_updates: list[tuple[str, dict]] = []

    def update_legacy_page_fields(self, page_id: str, **fields) -> None:
        self.legacy_updates.append((page_id, fields))


def _page(page_id: str, *, title: str = "Page", status: str = "published", updated_at: str = "2026-07-09T00:00:00") -> dict:
    return {
        "id": page_id,
        "title": title,
        "content": "body",
        "source_ids": "[]",
        "tags": "[]",
        "concept_summary": "summary",
        "status": status,
        "created_at": "2026-07-09T00:00:00",
        "updated_at": updated_at,
    }


def test_run_projects_lint_score_without_direct_db_write(monkeypatch):
    db = _NoDirectLintDb([_page("page-1")])
    projection = _FakeProjection()
    report = WikiLint(db, projection=projection).run()

    assert report["total_pages"] == 1
    assert projection.legacy_updates == [("page-1", {"lint_score": 0.85})]


def test_mark_complex_anomaly_projects_compatibility_field_without_direct_db_write(monkeypatch):
    projection = _FakeProjection()
    monkeypatch.setattr(WikiLint, "_canonical_projection", staticmethod(lambda: projection), raising=False)
    monkeypatch.setattr(WikiLint, "_default_db", staticmethod(lambda: _NoDirectLintDb([])))

    WikiLint.mark_complex_anomaly("page-1", ["empty", "orphan"])

    assert projection.legacy_updates == [("page-1", {"complex_anomaly": "empty,orphan"})]


def test_repair_duplicate_saves_canonical_status_without_direct_db_write(monkeypatch):
    latest = _page("page-new", title="Duplicate", updated_at="2026-07-10T00:00:00")
    old = _page("page-old", title="Duplicate", updated_at="2026-07-09T00:00:00")
    db = _NoDirectLintDb([latest, old])
    projection = _FakeProjection()
    linter = WikiLint(db, projection=projection)
    saved: list[tuple[str, dict, dict]] = []

    def save_canonical_page(page_id: str, legacy_page: dict, **fields) -> None:
        saved.append((page_id, legacy_page, fields))

    monkeypatch.setattr(linter, "_save_canonical_page", save_canonical_page, raising=False)
    monkeypatch.setattr(WikiLint, "_default_db", staticmethod(lambda: db))

    result = linter.repair_complex_issues([
        {"page_id": "page-new", "categories": ["duplicate"]},
    ])

    assert result["duplicate_fixed"] == 1
    assert saved == [("page-old", old, {"status": "deprecated"})]


def _canonical_services(tmp_path) -> tuple[WikiRepository, WikiProjection]:
    wiki_dir = tmp_path / "canonical-wiki"
    repository = WikiRepository(
        wiki_dir=wiki_dir,
        registry_path=wiki_dir / "_meta" / "pages.json",
        redirects_path=wiki_dir / "_meta" / "redirects.json",
        outbox_path=tmp_path / "wiki_projection_outbox.jsonl",
    )
    return repository, WikiProjection(repository, Database, enabled=True)


def _insert_legacy_page(page: dict) -> None:
    Database.insert_wiki_page({
        **page,
        "lint_score": 1.0,
        "complex_anomaly": "",
    })


def test_repair_duplicate_projects_canonical_status_to_injected_database(tmp_path):
    latest = _page("page-new", title="Duplicate", updated_at="2026-07-10T00:00:00")
    old = _page("page-old", title="Duplicate", updated_at="2026-07-09T00:00:00")
    _insert_legacy_page(latest)
    _insert_legacy_page(old)
    repository, projection = _canonical_services(tmp_path)

    result = WikiLint(
        Database,
        repository=repository,
        projection=projection,
    ).repair_complex_issues([
        {"page_id": "page-new", "categories": ["duplicate"]},
    ])

    assert result["duplicate_fixed"] == 1
    assert repository.get_page("page-old").status.value == "deprecated"
    assert Database.get_wiki_page("page-old")["status"] == "deprecated"


def test_custom_repository_without_projection_keeps_repository_and_database_paired(tmp_path):
    latest = _page("page-new", title="Duplicate", updated_at="2026-07-10T00:00:00")
    old = _page("page-old", title="Duplicate", updated_at="2026-07-09T00:00:00")
    _insert_legacy_page(latest)
    _insert_legacy_page(old)
    repository, _ = _canonical_services(tmp_path)

    result = WikiLint(repository=repository).repair_complex_issues([
        {"page_id": "page-new", "categories": ["duplicate"]},
    ])

    assert result["duplicate_fixed"] == 1
    assert repository.get_page("page-old").status.value == "deprecated"
    assert Database.get_wiki_page("page-old")["status"] == "deprecated"


def test_canonical_save_projects_content_source_ids_and_status_to_legacy_database(tmp_path):
    legacy = _page("page-1", status="draft")
    _insert_legacy_page(legacy)
    repository, projection = _canonical_services(tmp_path)

    WikiWorkflow._save_canonical_page(
        "page-1",
        legacy,
        content="Canonical replacement body",
        source_ids=["knowledge-1", "knowledge-2"],
        status="published",
        repository=repository,
        projection=projection,
    )

    canonical = repository.get_page("page-1")
    legacy_row = Database.get_wiki_page("page-1")
    assert canonical.body.strip() == "Canonical replacement body"
    assert canonical.source_ids == ["knowledge-1", "knowledge-2"]
    assert canonical.status.value == "published"
    assert legacy_row["content"] == canonical.body
    assert legacy_row["source_ids"] == '["knowledge-1", "knowledge-2"]'
    assert legacy_row["status"] == "published"

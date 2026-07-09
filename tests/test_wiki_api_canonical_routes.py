"""API wiki write routes should use canonical repository writes."""
from types import SimpleNamespace

from src.api.routes.wiki import (
    WikiPageWriteReq,
    create_wiki_page,
    delete_wiki_page,
    update_wiki_page,
)
from src.models.wiki_v2 import PageStatus, PageType, WikiPage


class _NoDirectWikiDb:
    def __init__(self, page: dict | None = None) -> None:
        self.page = page
        self.versions: list[tuple[str, dict]] = []

    def insert_wiki_page(self, page: dict) -> str:
        raise AssertionError("route must not call db.insert_wiki_page")

    def update_wiki_page(self, page_id: str, **fields):
        raise AssertionError("route must not call db.update_wiki_page")

    def delete_wiki_page(self, page_id: str):
        raise AssertionError("route must not call db.delete_wiki_page")

    def get_wiki_page(self, page_id: str) -> dict | None:
        return self.page if self.page and self.page["id"] == page_id else None

    def save_wiki_version(self, page_id: str, page: dict) -> str:
        self.versions.append((page_id, dict(page)))
        return "version-1"


class _FakeRepo:
    def __init__(self, page: WikiPage | None = None) -> None:
        self.pages: dict[str, WikiPage] = {}
        if page is not None:
            self.pages[page.page_id] = page
        self.saved: list[WikiPage] = []
        self.moved: list[tuple[str, str, str | None]] = []

    def save_page(self, page: WikiPage, expected_revision=None):
        page.revision = (self.pages.get(page.page_id).revision if page.page_id in self.pages else 0) + 1
        self.pages[page.page_id] = page
        self.saved.append(page)
        return SimpleNamespace(ok=True, object_id=page.page_id, revision=page.revision)

    def get_page(self, page_id: str) -> WikiPage | None:
        return self.pages.get(page_id)

    def move_page(self, page_id: str, new_title: str, new_page_type: str | None = None):
        self.moved.append((page_id, new_title, new_page_type))
        page = self.pages[page_id]
        page.title = new_title
        if new_page_type is not None:
            page.page_type = PageType(new_page_type)
        return SimpleNamespace(ok=True, object_id=page_id, revision=page.revision + 1)


class _FakeProjection:
    def __init__(self) -> None:
        self.processed = 0

    def process_outbox(self):
        self.processed += 1
        return SimpleNamespace(processed=1, errors=[], warnings=[])


def _page(page_id: str = "page-1") -> WikiPage:
    return WikiPage(
        schema_version=1,
        page_id=page_id,
        title="Old",
        page_type=PageType.SYNTHESES,
        status=PageStatus.DRAFT,
        revision=1,
        aliases=[],
        tags=[],
        source_ids=[],
        claim_ids=[],
        created_at="2026-07-09T00:00:00",
        updated_at="2026-07-09T00:00:00",
        content_hash="sha256:old",
        body="Old body",
    )


def test_create_wiki_page_uses_canonical_repository_without_direct_db_write():
    repo = _FakeRepo()
    projection = _FakeProjection()
    container = SimpleNamespace(db=_NoDirectWikiDb(), wiki_repository=repo, wiki_projection=projection)

    result = create_wiki_page(WikiPageWriteReq(title="Web Wiki", content="Draft"), container)

    assert result["id"] == repo.saved[0].page_id
    assert repo.saved[0].title == "Web Wiki"
    assert repo.saved[0].body == "Draft"
    assert repo.saved[0].status == PageStatus.DRAFT
    assert projection.processed == 1


def test_update_wiki_page_uses_canonical_repository_without_direct_db_write():
    page = _page()
    repo = _FakeRepo(page)
    projection = _FakeProjection()
    legacy_page = {"id": page.page_id, "title": page.title, "content": page.body, "status": "draft"}
    container = SimpleNamespace(db=_NoDirectWikiDb(legacy_page), wiki_repository=repo, wiki_projection=projection)

    result = update_wiki_page(page.page_id, WikiPageWriteReq(title="New", content="Updated"), container)

    assert result["message"] == "保存成功"
    assert repo.moved == [(page.page_id, "New", PageType.SYNTHESES.value)]
    assert repo.saved[-1].title == "New"
    assert repo.saved[-1].body == "Updated"
    assert container.db.versions == [(page.page_id, legacy_page)]
    assert projection.processed == 1


def test_delete_wiki_page_uses_canonical_repository_without_direct_db_write():
    page = _page()
    repo = _FakeRepo(page)
    projection = _FakeProjection()
    legacy_page = {"id": page.page_id, "title": page.title, "content": page.body, "status": "draft"}
    container = SimpleNamespace(db=_NoDirectWikiDb(legacy_page), wiki_repository=repo, wiki_projection=projection)

    result = delete_wiki_page(page.page_id, container)

    assert result["page_id"] == page.page_id
    assert repo.saved[-1].status.value == "deleted"
    assert projection.processed == 1

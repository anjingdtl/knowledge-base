"""WikiWorkflow state changes should write through canonical repository."""
from types import SimpleNamespace

from src.models.wiki_v2 import PageStatus, PageType, WikiPage
from src.services.wiki_workflow import WikiWorkflow


class _NoDirectWorkflowDb:
    def __init__(self, page: dict, version: dict | None = None) -> None:
        self.page = page
        self.version = version
        self.saved_versions: list[tuple[str, dict]] = []
        self.workflow: list[tuple[str, str, str, str, str]] = []

    def get_wiki_page(self, page_id: str) -> dict | None:
        return self.page if self.page["id"] == page_id else None

    def get_wiki_version(self, page_id: str, version: int) -> dict | None:
        return self.version if self.version and self.version["page_id"] == page_id else None

    def save_wiki_version(self, page_id: str, page: dict) -> str:
        self.saved_versions.append((page_id, dict(page)))
        return "version-1"

    def update_wiki_page(self, page_id: str, **fields):
        raise AssertionError("workflow must not call Database.update_wiki_page")

    def insert_workflow(self, page_id: str, from_status: str, to_status: str, operator: str, comment: str) -> str:
        self.workflow.append((page_id, from_status, to_status, operator, comment))
        return "workflow-1"


class _FakeRepo:
    def __init__(self, page: WikiPage) -> None:
        self.page = page
        self.saved: list[WikiPage] = []

    def get_page(self, page_id: str) -> WikiPage | None:
        return self.page if self.page.page_id == page_id else None

    def move_page(self, page_id: str, new_title: str, new_page_type: str | None = None):
        self.page.title = new_title
        if new_page_type is not None:
            self.page.page_type = PageType(new_page_type)
        return SimpleNamespace(ok=True, object_id=page_id, revision=self.page.revision + 1)

    def save_page(self, page: WikiPage, expected_revision=None):
        self.page = page
        self.saved.append(page)
        return SimpleNamespace(ok=True, object_id=page.page_id, revision=page.revision + 1)


class _FakeProjection:
    def __init__(self) -> None:
        self.processed = 0
        self.legacy_updates: list[tuple[str, dict]] = []

    def process_outbox(self, *, force: bool = False):
        self.processed += 1
        return SimpleNamespace(processed=1, errors=[], warnings=[])

    def update_legacy_page_fields(self, page_id: str, **fields):
        self.legacy_updates.append((page_id, fields))


def _legacy_page(status: str = "draft") -> dict:
    return {
        "id": "page-1",
        "title": "Old",
        "content": "Old body",
        "source_ids": "[]",
        "tags": "[]",
        "concept_summary": "Old summary",
        "status": status,
        "created_at": "2026-07-09T00:00:00",
        "updated_at": "2026-07-09T00:00:00",
    }


def _canonical_page(status: PageStatus = PageStatus.DRAFT) -> WikiPage:
    return WikiPage(
        schema_version=1,
        page_id="page-1",
        title="Old",
        page_type=PageType.SYNTHESES,
        status=status,
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


def test_submit_for_review_updates_canonical_status_without_direct_db_write(monkeypatch):
    db = _NoDirectWorkflowDb(_legacy_page("draft"))
    repo = _FakeRepo(_canonical_page(PageStatus.DRAFT))
    projection = _FakeProjection()
    monkeypatch.setattr("src.services.wiki_workflow.Database", db)
    monkeypatch.setattr("src.services.wiki_workflow.Config.get", lambda key, default=None: False)
    monkeypatch.setattr(
        "src.core.container.get_active_container",
        lambda: SimpleNamespace(wiki_repository=repo, wiki_projection=projection),
    )

    result = WikiWorkflow.submit_for_review("page-1", operator="tester", comment="please review")

    assert result.success is True
    assert repo.saved[-1].status == PageStatus.REVIEW
    assert projection.processed == 1
    assert db.workflow == [("page-1", "draft", "review", "tester", "please review")]


def test_restore_version_updates_canonical_page_without_direct_db_write(monkeypatch):
    version = {
        "page_id": "page-1",
        "title": "Restored",
        "content": "Restored body",
        "concept_summary": "Restored summary",
        "tags": '["restored"]',
    }
    db = _NoDirectWorkflowDb(_legacy_page("published"), version=version)
    repo = _FakeRepo(_canonical_page(PageStatus.PUBLISHED))
    projection = _FakeProjection()
    monkeypatch.setattr("src.services.wiki_workflow.Database", db)
    monkeypatch.setattr(
        "src.core.container.get_active_container",
        lambda: SimpleNamespace(wiki_repository=repo, wiki_projection=projection),
    )

    result = WikiWorkflow.restore_version("page-1", 1)

    assert result.success is True
    assert repo.saved[-1].title == "Restored"
    assert repo.saved[-1].body == "Restored body"
    assert repo.saved[-1].status == PageStatus.DRAFT
    assert repo.saved[-1].tags == ["restored"]
    assert projection.legacy_updates == [("page-1", {"concept_summary": "Restored summary"})]


def test_canonical_save_updates_source_ids(monkeypatch):
    repo = _FakeRepo(_canonical_page())
    projection = _FakeProjection()
    monkeypatch.setattr(
        "src.core.container.get_active_container",
        lambda: SimpleNamespace(wiki_repository=repo, wiki_projection=projection),
    )

    WikiWorkflow._save_canonical_page(
        "page-1",
        _legacy_page(),
        source_ids=["knowledge-1", "knowledge-2"],
    )

    assert repo.saved[-1].source_ids == ["knowledge-1", "knowledge-2"]

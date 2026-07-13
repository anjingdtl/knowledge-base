"""WikiV2Migrator 迁移器测试(Phase 6 T6.1)。

覆盖:dry-run 零写、已 canonical 跳过、Facts→draft claim、无来源事实、
同名冲突、apply 写入、lock、rollback、不强制 primary。
"""
from __future__ import annotations

from pathlib import Path

from src.models.wiki_v2 import PageStatus, PageType, WikiPage
from src.services.wiki_repository import WikiRepository
from src.services.wiki_v2_migrator import WikiV2Migrator


def _repo(tmp: Path) -> WikiRepository:
    wiki = tmp / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    return WikiRepository(
        wiki_dir=wiki,
        registry_path=wiki / "_meta" / "pages.json",
        redirects_path=wiki / "_meta" / "redirects.json",
        outbox_path=tmp / "outbox.jsonl",
    )


def _write_b_page(
    wiki: Path,
    rel: str,
    fm_title: str,
    body: str,
    *,
    page_id: str | None = None,
    source_ids: list[str] | None = None,
    knowledge_id: str | None = None,
) -> None:
    path = wiki / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"title: {fm_title}", "page_type: entities"]
    if page_id:
        lines.extend([
            f"page_id: {page_id}",
            "schema_version: 1",
            "status: published",
            "revision: 1",
            "claim_ids: []",
            "aliases: []",
            "tags: []",
            "created_at: t",
            "updated_at: t",
            "content_hash: h",
        ])
        sids = source_ids or []
        if sids:
            lines.append("source_ids:")
            for s in sids:
                lines.append(f"  - {s}")
        else:
            lines.append("source_ids: []")
    elif knowledge_id:
        lines.append(f"knowledge_id: {knowledge_id}")
        if source_ids:
            lines.append("source_ids:")
            for s in source_ids:
                lines.append(f"  - {s}")
    elif source_ids:
        lines.append("source_ids:")
        for s in source_ids:
            lines.append(f"  - {s}")
    lines += ["---", "", body]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_dry_run_zero_writes(tmp_path):
    repo = _repo(tmp_path)
    _write_b_page(
        repo._wiki_dir, "entities/alpha.md", "Alpha",
        "## Facts\n- Alpha is a product\n", knowledge_id="k1",
    )
    m = WikiV2Migrator(
        wiki_dir=repo._wiki_dir, repository=repo, backups_dir=tmp_path / "backups",
    )
    before = sorted(p.relative_to(repo._wiki_dir) for p in repo._wiki_dir.rglob("*") if p.is_file())
    report = m.dry_run()
    after = sorted(p.relative_to(repo._wiki_dir) for p in repo._wiki_dir.rglob("*") if p.is_file())
    assert report.mode == "dry_run"
    assert report.writes == 0
    assert report.b_page_count >= 1
    assert report.pages_to_create >= 1
    assert after == before


def test_dry_run_already_canonical_skipped(tmp_path):
    repo = _repo(tmp_path)
    page = WikiPage(
        schema_version=1, page_id="p-beta", title="Beta", page_type=PageType.ENTITIES,
        status=PageStatus.PUBLISHED, revision=1, aliases=[], tags=[], source_ids=["k2"],
        claim_ids=[], created_at="t", updated_at="t", content_hash="h", body="body",
    )
    with repo.transaction() as tx:
        tx.stage_page(page)
    m = WikiV2Migrator(
        wiki_dir=repo._wiki_dir, repository=repo, backups_dir=tmp_path / "backups",
    )
    report = m.dry_run()
    assert report.already_canonical >= 1
    assert report.pages_to_create == 0


def test_dry_run_extracts_facts_as_draft_claims(tmp_path):
    repo = _repo(tmp_path)
    _write_b_page(
        repo._wiki_dir, "entities/gamma.md", "Gamma",
        "## Facts\n- Gamma supports FTTR\n- Another fact\n", knowledge_id="k3",
    )
    m = WikiV2Migrator(
        wiki_dir=repo._wiki_dir, repository=repo, backups_dir=tmp_path / "backups",
    )
    report = m.dry_run()
    assert report.claims_to_create == 2
    assert all(c.status == "draft" for c in report.claim_plans)
    assert all(c.location_quality == "page_only" for c in report.claim_plans)


def test_dry_run_untraceable_facts(tmp_path):
    repo = _repo(tmp_path)
    _write_b_page(
        repo._wiki_dir, "concepts/no-src.md", "NoSrc",
        "## Facts\n- Orphan fact without source\n",
    )
    m = WikiV2Migrator(
        wiki_dir=repo._wiki_dir, repository=repo, backups_dir=tmp_path / "backups",
    )
    report = m.dry_run()
    assert report.untraceable_facts >= 1
    assert any(c.status in ("draft", "unsupported") for c in report.claim_plans)


def test_same_title_different_content_is_conflict(tmp_path):
    repo = _repo(tmp_path)

    class FakeDB:
        def list_wiki_pages(self, **kw):
            return [{
                "id": "a1",
                "title": "Dup",
                "content": "AAAA entirely different body text here about topic one",
                "source_ids": '["ka"]',
                "tags": "[]",
                "status": "active",
                "created_at": "t",
                "updated_at": "t",
            }]

    _write_b_page(
        repo._wiki_dir, "entities/dup.md", "Dup",
        "BBBB completely other content for conflict about topic two",
        knowledge_id="kb",
    )
    m = WikiV2Migrator(
        wiki_dir=repo._wiki_dir, repository=repo, database=FakeDB(),
        backups_dir=tmp_path / "backups",
    )
    report = m.dry_run()
    assert report.conflicts >= 1
    assert any(p.action == "conflict" for p in report.page_plans)


def test_apply_creates_canonical_pages_and_draft_claims(tmp_path):
    repo = _repo(tmp_path)
    _write_b_page(
        repo._wiki_dir, "entities/delta.md", "Delta",
        "## Facts\n- Delta fact one\n", knowledge_id="k4",
    )
    m = WikiV2Migrator(
        wiki_dir=repo._wiki_dir, repository=repo,
        backups_dir=tmp_path / "backups",
        clock=lambda: "20260713T120000",
        id_factory=iter([f"id-{i}" for i in range(1, 50)]).__next__,
    )
    report = m.apply()
    assert report.mode == "apply"
    assert report.writes > 0
    assert report.backup_path
    assert (tmp_path / "backups" / "wiki-v2-20260713T120000").exists()
    pages = repo.list_pages()
    assert any(p.title == "Delta" for p in pages)
    claims = repo.list_claims()
    assert len(claims) >= 1
    assert all(c.status.value in ("draft", "unsupported") for c in claims)


def test_apply_lock_prevents_concurrent(tmp_path):
    repo = _repo(tmp_path)
    lock = tmp_path / "backups" / ".wiki_v2_migration.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("held", encoding="utf-8")
    m = WikiV2Migrator(
        wiki_dir=repo._wiki_dir, repository=repo, backups_dir=tmp_path / "backups",
    )
    report = m.apply()
    assert report.errors
    assert report.writes == 0


def test_rollback_restores_wiki(tmp_path):
    repo = _repo(tmp_path)
    _write_b_page(
        repo._wiki_dir, "entities/eps.md", "Eps",
        "## Facts\n- Eps fact\n", knowledge_id="k5",
    )
    m = WikiV2Migrator(
        wiki_dir=repo._wiki_dir, repository=repo,
        backups_dir=tmp_path / "backups",
        clock=lambda: "20260713T130000",
        id_factory=iter([f"rid-{i}" for i in range(1, 50)]).__next__,
    )
    m.apply()
    marker = repo._wiki_dir / "entities" / "marker-corrupt.md"
    marker.write_text("corrupt", encoding="utf-8")
    rb = m.rollback("20260713T130000")
    assert rb.mode == "rollback"
    assert not marker.exists()


def test_apply_does_not_force_primary_mode(tmp_path):
    repo = _repo(tmp_path)
    cfg: dict = {"wiki": {"canonical_v2": {"mode": "off"}}}
    _write_b_page(
        repo._wiki_dir, "entities/zeta.md", "Zeta", "body", knowledge_id="k6",
    )
    m = WikiV2Migrator(
        wiki_dir=repo._wiki_dir, repository=repo,
        backups_dir=tmp_path / "backups", config=cfg,
        clock=lambda: "20260713T140000",
        id_factory=iter([f"zid-{i}" for i in range(1, 50)]).__next__,
    )
    report = m.apply()
    assert cfg["wiki"]["canonical_v2"]["mode"] == "off"
    assert report.suggestion

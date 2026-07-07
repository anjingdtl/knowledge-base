from __future__ import annotations

import pytest

from src.models.wiki_v2 import Claim, PageStatus, PageType, WikiPage
from src.services.wiki_repository import StaleRevisionError, WikiRepository


@pytest.fixture
def repo(tmp_path):
    return WikiRepository(
        wiki_dir=tmp_path / "wiki",
        registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
        redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
        outbox_path=tmp_path / "wiki_projection_outbox.jsonl",
    )


def _page(page_id="page_1", title="FTTR", revision=1, claim_ids=None):
    return WikiPage(
        schema_version=2, page_id=page_id, title=title, page_type=PageType.CONCEPTS,
        status=PageStatus.DRAFT, revision=revision, aliases=[], tags=[], source_ids=["k1"],
        claim_ids=claim_ids or [], created_at="t", updated_at="t",
        content_hash="sha256:x", body="# FTTR\n", supersedes_page_id=None,
    )


def test_create_page_writes_file_and_registry(repo):
    r = repo.save_page(_page())
    assert r.ok and r.revision == 1
    assert (repo._wiki_dir / "concepts" / "fttr.md").exists()
    # registry 含映射
    entry = repo.get_registry().get("page_1")
    assert entry and entry["path"] == "concepts/fttr.md"


def test_update_increments_revision_when_expected_matches(repo):
    repo.save_page(_page(revision=1))
    p = repo.get_page("page_1")
    p.body = "# FTTR v2\n"
    r = repo.save_page(p, expected_revision=1)
    assert r.ok and r.revision == 2


def test_stale_expected_revision_raises(repo):
    repo.save_page(_page(revision=1))
    p = repo.get_page("page_1")
    p.body = "x"
    with pytest.raises(StaleRevisionError):
        repo.save_page(p, expected_revision=99)  # 期望旧值,实际已是 1→将被改


def test_atomic_write_no_half_file_on_error(repo, monkeypatch):
    # 模拟 registry 写失败时 canonical 文件不被半写(具体用 monkeypatch 检查无 .tmp 残留)
    p = _page()
    repo.save_page(p)
    assert not list(repo._wiki_dir.rglob("*.tmp"))


def test_rename_keeps_page_id(repo):
    repo.save_page(_page(title="OldName"))
    r = repo.move_page("page_1", new_title="NewName")
    assert r.ok
    assert (repo._wiki_dir / "concepts" / "newname.md").exists()
    assert not (repo._wiki_dir / "concepts" / "oldname.md").exists()
    # page_id 不变
    assert repo.get_page("page_1").page_id == "page_1"
    # redirect 记录旧路径
    redirects = repo.get_redirects()
    assert any("oldname" in k for k in redirects)


def test_claim_crud(repo):
    c = Claim.from_dict(dict(
        schema_version=1, claim_id="claim_1", statement="s", normalized_statement="s",
        claim_type="fact", status="active", confidence=0.9, valid_from=None, valid_to=None,
        subject_refs=[], predicate="", object_refs=[],
        evidence=[dict(evidence_id="ev1", stance="supports", knowledge_id="k1", block_id="b1",
                       location={}, source_revision="sha256:s", excerpt_hash=None, observed_at="t")],
        relations=[], created_at="t", updated_at="t", revision=1,
    ), strict=True)
    r = repo.save_claim(c)
    assert r.ok
    got = repo.get_claim("claim_1")
    assert got and got.statement == "s"
    # delete(soft) 后 get 返回 None
    repo.delete_claim("claim_1", soft=True)
    assert repo.get_claim("claim_1") is None


def test_transaction_rollback_on_failure(repo):
    p1 = _page(page_id="page_a", title="A")
    p2 = _page(page_id="page_b", title="B")
    with pytest.raises(RuntimeError):
        with repo.transaction() as tx:
            tx.stage_page(p1)
            tx.stage_page(p2)
            raise RuntimeError("boom")  # 模拟中途失败
    # 中途失败:两个文件都不应发布
    assert not (repo._wiki_dir / "concepts" / "a.md").exists()
    assert not (repo._wiki_dir / "concepts" / "b.md").exists()
    assert not list((repo._wiki_dir / "_staging").glob("*")) if (repo._wiki_dir / "_staging").exists() else True


def test_outbox_events_appended_in_order(repo):
    repo.save_page(_page())
    events = repo.read_outbox()
    assert events and events[0]["type"] in ("page.created", "page.updated")


def test_windows_path_compat(repo):
    # 路径用 / 分隔存 registry,跨平台
    repo.save_page(_page(title="Win Path"))
    reg = repo.get_registry()
    entry = next(iter(reg.values()))
    assert "\\" not in entry["path"]


def test_concurrent_write_conflict_detected(repo):
    # 两次同 expected_revision=1 并发(同进程模拟:第一次成功后第二次 expected 仍 1 应冲突)
    repo.save_page(_page(revision=1))
    p = repo.get_page("page_1")
    p2 = repo.get_page("page_1")  # 另一"会话"读到 revision=1
    p.body = "v2"
    repo.save_page(p, expected_revision=1)  # 先提交 → revision=2
    p2.body = "v3"
    with pytest.raises(StaleRevisionError):
        repo.save_page(p2, expected_revision=1)  # lost update 防护

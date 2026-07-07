from __future__ import annotations

from src.models.wiki_v2 import WikiPage
from src.services.wiki_validator import WikiValidator


def _page_dict(**over):
    d = dict(schema_version=2, page_id="page_abc", title="FTTR", page_type="concepts",
             status="draft", revision=1, aliases=[], tags=[], source_ids=["k1"], claim_ids=[],
             created_at="2026-07-07T12:00:00+08:00", updated_at="2026-07-07T12:00:00+08:00",
             content_hash="sha256:x", body="# FTTR\n")
    d.update(over)
    return d


def test_validate_clean_page_no_findings():
    v = WikiValidator()
    findings = v.validate_page(WikiPage.from_dict(_page_dict(), strict=True))
    assert findings == []


def test_validate_page_schema_error_category():
    v = WikiValidator()
    d = _page_dict(status="bogus")
    findings = v.validate_page_dict(d)  # 接受 dict,内部 try from_dict
    assert any(f.category == "schema_invalid" and f.severity == "error" for f in findings)


def test_published_page_referencing_draft_claim_flagged(tmp_path):
    v = WikiValidator()
    page = WikiPage.from_dict(_page_dict(status="published", claim_ids=["claim_x"]), strict=True)
    # validator 接受一个 claim_store 查询函数
    def claim_lookup(cid):
        from src.models.wiki_v2 import ClaimStatus
        # 模拟 claim_x 处于 draft
        class _C:
            status = ClaimStatus.DRAFT
        return _C()
    findings = v.validate_page(page, claim_lookup=claim_lookup)
    assert any(f.category == "publish_gate_violation" for f in findings)


def test_validate_directory_reports_missing_claim_files(tmp_path):
    (tmp_path / "concepts").mkdir()
    (tmp_path / "concepts" / "fttr.md").write_text(
        "---\nschema_version: 2\npage_id: page_abc\ntitle: FTTR\npage_type: concepts\n"
        "status: draft\nrevision: 1\nsource_ids: []\nclaim_ids: [claim_missing]\n"
        "created_at: t\nupdated_at: t\ncontent_hash: x\n---\n\n# FTTR\n", encoding="utf-8")
    v = WikiValidator(wiki_dir=tmp_path)
    findings = v.validate_directory()
    assert any(f.category == "claim_missing" and f.object_id == "page_abc" for f in findings)

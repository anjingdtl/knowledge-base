"""WikiPageLocator 单元测试(第二阶段 Task 1.0)。

覆盖 plan Step 1.0.1 的 5 个失败测试:命中匹配 / 零命中 / title+frontmatter+body
三通道 / 候选 schema / wiki 目录缺失降级。
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

from src.services.wiki_page_locator import WikiPageLocator
from src.services.wiki_slug import write_markdown


def _make_page(
    wiki_dir: Path,
    page_type: str,
    slug: str,
    title: str,
    body: str,
    key_entities: list[str] | None = None,
    page_id: str | None = None,
) -> Path:
    """在临时 wiki 目录下生成一个 wiki 页(带 frontmatter)。"""
    d = wiki_dir / page_type
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.md"
    fm: dict = {"title": title}
    if key_entities is not None:
        fm["key_entities"] = key_entities
    if page_id is not None:
        fm["page_id"] = page_id
    write_markdown(path, fm, body)
    return path


def test_locate_returns_matching_pages(tmp_path):
    _make_page(tmp_path, "sources", "fttr", "FTTR光纤",
               "FTTR 是光纤到房间的技术", key_entities=["FTTR"])
    _make_page(tmp_path, "sources", "marketing", "营销通知", "本月营销活动安排")
    _make_page(tmp_path, "sources", "unrelated", "天气", "今天天气不错")

    locator = WikiPageLocator(wiki_dir=tmp_path)
    candidates, total = locator.locate("FTTR")

    assert total >= 1
    titles = [c["metadata"]["title"] for c in candidates]
    assert any("FTTR" in t for t in titles)
    # marketing / unrelated 不含 FTTR,不应命中
    assert not any("天气" in t for t in titles)


def test_locate_no_match_returns_empty(tmp_path):
    _make_page(tmp_path, "sources", "fttr", "FTTR", "FTTR 光纤",
               key_entities=["FTTR"])
    locator = WikiPageLocator(wiki_dir=tmp_path)
    candidates, total = locator.locate("zzznotexist9999")
    assert candidates == []
    assert total == 0


def test_locate_searches_title_frontmatter_body(tmp_path):
    # title / key_entities 均不含,仅 body 含独特 token —— 验证 body 通道
    _make_page(tmp_path, "sources", "doc", "无关标题文档",
               "本文介绍 UNIQUETERM 的实现细节", key_entities=["其他实体"])
    locator = WikiPageLocator(wiki_dir=tmp_path)
    candidates, total = locator.locate("UNIQUETERM")
    assert total >= 1
    assert candidates[0]["metadata"]["title"] == "无关标题文档"


def test_locate_candidate_schema(tmp_path):
    _make_page(tmp_path, "sources", "fttr", "FTTR", "FTTR 光纤",
               key_entities=["FTTR"])
    locator = WikiPageLocator(wiki_dir=tmp_path)
    candidates, _ = locator.locate("FTTR")
    assert candidates, "应至少命中一页"
    c = candidates[0]
    for field in ("id", "text", "metadata", "match_channels"):
        assert field in c, f"候选缺字段 {field}"
    assert c["match_channels"] == ["wiki_read"]
    assert isinstance(c["id"], str) and c["id"].startswith("wiki:")
    assert isinstance(c["metadata"], dict)


def test_locate_missing_wiki_dir_returns_empty(tmp_path):
    locator = WikiPageLocator(wiki_dir=tmp_path / "does_not_exist")
    candidates, total = locator.locate("anything")
    assert candidates == []
    assert total == 0


# ---- T2.3 新增: stable page_id + projection enrichment ----


def test_locate_id_uses_frontmatter_page_id(tmp_path):
    """canonical 页 frontmatter 有 page_id -> 候选 id 用 page_id 而非 slug。"""
    _make_page(
        tmp_path, "sources", "my-slug", "FTTR光纤",
        "FTTR 是光纤到房间的技术",
        page_id="page_abc",
    )
    locator = WikiPageLocator(wiki_dir=tmp_path)
    candidates, total = locator.locate("FTTR")
    assert total >= 1
    c = candidates[0]
    assert c["id"] == "wiki:sources:page_abc"
    assert c["metadata"]["page_id"] == "page_abc"


def test_locate_id_fallback_slug_when_no_page_id(tmp_path):
    """legacy 页无 frontmatter page_id -> 候选 id 用 slug(旧行为)。"""
    _make_page(tmp_path, "sources", "fttr", "FTTR光纤", "FTTR 是光纤到房间的技术")
    locator = WikiPageLocator(wiki_dir=tmp_path)
    candidates, total = locator.locate("FTTR")
    assert total >= 1
    c = candidates[0]
    assert c["id"] == "wiki:sources:fttr"
    assert c["metadata"]["page_id"] is None


def test_locate_metadata_exposes_page_id(tmp_path):
    """canonical 和 legacy 页 metadata 都含 page_id 键。"""
    _make_page(
        tmp_path, "sources", "canon", "Canonical页", "内容",
        page_id="pid_canonical",
    )
    _make_page(tmp_path, "sources", "legacy", "Legacy页", "内容")
    locator = WikiPageLocator(wiki_dir=tmp_path)
    candidates, _ = locator.locate("内容")
    assert len(candidates) == 2
    meta_map = {c["metadata"]["title"]: c["metadata"] for c in candidates}
    assert meta_map["Canonical页"]["page_id"] == "pid_canonical"
    assert meta_map["Legacy页"]["page_id"] is None


def test_locate_projection_enriches_legacy_page_id(tmp_path):
    """projection 有数据时补全 legacy 页的 page_id,改 id 和 metadata。"""
    _make_page(tmp_path, "sources", "fttr", "FTTR光纤", "FTTR 是光纤到房间的技术")
    # 构造 projection 并手动插入一行 wiki_pages_v2
    proj = MagicMock()
    proj.find_page_id_by_path.return_value = "proj_page_001"

    locator = WikiPageLocator(wiki_dir=tmp_path, projection=proj)
    candidates, total = locator.locate("FTTR")
    assert total >= 1
    c = candidates[0]
    assert c["id"] == "wiki:sources:proj_page_001"
    assert c["metadata"]["page_id"] == "proj_page_001"
    assert c["metadata"]["canonical"] is True
    proj.find_page_id_by_path.assert_called_once()


def test_locate_projection_failure_falls_back_to_fs(tmp_path, caplog):
    """projection 异常时 FS fallback,不抛,记 warning(限一次)。"""
    _make_page(tmp_path, "sources", "fttr", "FTTR光纤", "FTTR 是光纤到房间的技术")
    proj = MagicMock()
    proj.find_page_id_by_path.side_effect = RuntimeError("db gone")

    with caplog.at_level(logging.WARNING, logger="src.services.wiki_page_locator"):
        locator = WikiPageLocator(wiki_dir=tmp_path, projection=proj)
        candidates, total = locator.locate("FTTR")

    assert total >= 1
    # FS fallback -> slug id, page_id is None
    c = candidates[0]
    assert c["id"] == "wiki:sources:fttr"
    assert c["metadata"]["page_id"] is None
    assert any("fallback" in r.message.lower() or "projection" in r.message.lower() for r in caplog.records)


def test_locate_real_projection_enriches_with_forward_slash_path(tmp_path):
    """真实 WikiProjection + 真实 DB + 真实 FS:验证 Windows 路径分隔符归一化。

    在 wiki_pages_v2.path 中用 forward-slash 存储路径(与 WikiRepository._rel 一致),
    locator 的 _enrich_with_projection 必须将 Windows backslash 路径转为 forward-slash
    才能匹配。用 .as_posix() 后此测试在 Windows 上通过;用 str() 则会失败。
    """
    from src.services.db import Database
    from src.services.wiki_projection import WikiProjection
    from src.services.wiki_repository import WikiRepository

    # 1. 在 FS 写一个 legacy 页 (无 page_id)
    write_markdown(
        tmp_path / "sources" / "fttr.md",
        {"title": "FTTR", "key_entities": ["FTTR"]},
        "FTTR 光纤",
    )

    # 2. 真实 WikiRepository + WikiProjection
    data_dir = tmp_path / "canonical"
    data_dir.mkdir()
    repo = WikiRepository(
        wiki_dir=str(tmp_path),
        registry_path=str(data_dir / "registry.json"),
        redirects_path=str(data_dir / "redirects.json"),
        outbox_path=str(data_dir / "outbox.jsonl"),
    )
    db = Database._instance
    proj = WikiProjection(repo, db, enabled=True)

    # 3. 插入 projection 行 — path 用 forward-slash (生产实际存储方式)
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO wiki_pages_v2 "
        "(page_id, path, title, page_type, status, revision, content, content_hash, "
        "aliases_json, tags_json, source_ids_json, claim_ids_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "page_proj_1", "sources/fttr.md", "FTTR", "sources", "published",
            1, "FTTR 光纤", "abc123", "[]", "[]", "[]", "[]",
            "2026-07-08T00:00:00Z", "2026-07-08T00:00:00Z",
        ),
    )
    conn.commit()

    # 4. locate 并断言 enrichment 发生
    locator = WikiPageLocator(wiki_dir=tmp_path, projection=proj)
    cands, _ = locator.locate("FTTR")
    assert len(cands) >= 1
    c = cands[0]
    # enrichment 将 slug id "wiki:sources:fttr" 改为 projection page_id
    assert c["id"] == "wiki:sources:page_proj_1"
    assert c["metadata"]["page_id"] == "page_proj_1"
    assert c["metadata"]["canonical"] is True


def test_locate_disabled_projection_skips_db_queries(tmp_path):
    """projection.enabled=False 时 _enrich_with_projection 跳过,不触 DB 查询。"""
    _make_page(tmp_path, "sources", "fttr", "FTTR光纤", "FTTR 是光纤到房间的技术")
    proj = MagicMock()
    proj.enabled = False
    proj.find_page_id_by_path.return_value = "should_not_be_called"

    locator = WikiPageLocator(wiki_dir=tmp_path, projection=proj)
    candidates, total = locator.locate("FTTR")
    assert total >= 1
    c = candidates[0]
    # enrichment 被跳过 -> slug id, page_id is None
    assert c["id"] == "wiki:sources:fttr"
    assert c["metadata"]["page_id"] is None
    proj.find_page_id_by_path.assert_not_called()

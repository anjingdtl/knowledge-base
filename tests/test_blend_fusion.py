"""blend RRF 融合单元测试(第二阶段 Task 1.3)。

覆盖:两路合并 / RRF 公式(k=40) / 统一候选 schema / wiki 空候选回退 / 同 id 累加并集。
"""
from __future__ import annotations

from src.services.blend_fusion import blend_fusion


def _wiki_cand(slug: str = "fttr", channels=None) -> dict:
    return {
        "id": f"wiki:sources:{slug}",
        "text": f"wiki body {slug}",
        "metadata": {"title": slug, "page_type": "sources"},
        "match_channels": channels if channels is not None else ["wiki_read"],
    }


def _search_cand(cid: str = "page1:block1", channels=None) -> dict:
    return {
        "id": cid,
        "text": "search body",
        "metadata": {"page_id": "page1", "block_id": "block1"},
        "match_channels": channels if channels is not None else ["semantic", "keyword"],
    }


def test_blend_fusion_merges_two_channels():
    result = blend_fusion([_wiki_cand()], [_search_cand()])
    assert len(result) == 2
    ids = {c["id"] for c in result}
    assert "wiki:sources:fttr" in ids
    assert "page1:block1" in ids
    # 按 rrf_score 降序
    scores = [c["rrf_score"] for c in result]
    assert scores == sorted(scores, reverse=True)


def test_blend_rrf_formula():
    # wiki 单候选 rank=0, w=0.5, k=40 → 0.5/(40+0+1)
    result = blend_fusion([_wiki_cand()], [], w_wiki=0.5, k=40)
    assert len(result) == 1
    expected = 0.5 / (40 + 0 + 1)
    assert abs(result[0]["rrf_score"] - round(expected, 6)) < 1e-9
    assert result[0]["final_score"] == expected


def test_blend_candidate_schema_unified():
    result = blend_fusion([_wiki_cand()], [_search_cand()])
    assert len(result) == 2
    for c in result:
        for field in ("id", "text", "metadata", "match_channels", "rrf_score", "final_score"):
            assert field in c, f"候选缺字段 {field}"
        assert isinstance(c["match_channels"], list)


def test_blend_no_wiki_candidates_falls_back_to_full_search():
    search = [_search_cand("p1:b1"), _search_cand("p2:b2")]
    result = blend_fusion([], search)
    assert len(result) == 2
    assert {c["id"] for c in result} == {"p1:b1", "p2:b2"}


def test_blend_channels_union_when_id_collides():
    # 同 id 两路命中 → RRF 累加 + match_channels 并集
    cand_w = {"id": "shared", "text": "w", "metadata": {}, "match_channels": ["wiki_read"]}
    cand_s = {"id": "shared", "text": "s", "metadata": {}, "match_channels": ["semantic"]}
    result = blend_fusion([cand_w], [cand_s])
    assert len(result) == 1
    assert set(result[0]["match_channels"]) == {"wiki_read", "semantic"}
    # 累加分 > 单路分
    single = blend_fusion([cand_w], [])
    assert result[0]["rrf_score"] > single[0]["rrf_score"]

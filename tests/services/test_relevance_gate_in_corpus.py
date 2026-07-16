"""In-corpus institutional evidence must not be false-refused."""
from __future__ import annotations

from src.services.relevance_gate import (
    evaluate_evidence,
    extract_query_terms,
    is_current_information_query,
)


def test_extract_query_terms_splits_long_cjk_run() -> None:
    terms = extract_query_terms("企业微信运营管理办法的主题是什么")
    assert "企业微信" in terms or "运营管理" in terms or "管理办法" in terms
    # should not be only one mega-term of the whole sentence
    assert not any(len(t) > 12 for t in terms)


def test_accepts_strong_title_match_even_if_semantic_score_low() -> None:
    items = [
        {
            "knowledge_id": "d1",
            "title": "中国电信广西公司企业微信运营管理办法",
            "text": "本办法规范企业微信运营管理相关要求。",
            "score": 0.1,
        }
    ]
    decision = evaluate_evidence(
        "企业微信运营管理办法的主题是什么",
        items,
        threshold=0.35,
    )
    assert decision["accept"] is True
    assert decision["items"]
    assert decision["items"][0]["knowledge_id"] == "d1"


def test_current_info_still_rejected() -> None:
    assert is_current_information_query("今天公司营收是多少") is True
    decision = evaluate_evidence(
        "今天公司营收是多少",
        [{"knowledge_id": "x", "title": "预算", "text": "营收相关历史说明", "score": 0.9}],
        threshold=0.35,
    )
    assert decision["accept"] is False
    assert decision["reason"] == "requires_current_external_data"

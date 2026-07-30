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


# --- SPEC Phase 3.2: alias-expanded synonym terms -----------------------------


def test_extract_query_terms_includes_alias_synonym_words() -> None:
    """Alias-expanded synonym words (竞赛/门店) must be included in the term
    set so candidates surfaced by alias FTS variants receive proper coverage
    credit. Without this, a formal-term candidate (e.g. "劳动竞赛奖励办法")
    matched only by the alias variant (竞赛) would score too low to clear the
    relevance gate even though FTS verified the lexical match.
    """
    terms = extract_query_terms("比赛的奖金是多少")
    # Original query terms still present
    assert "比赛" in terms
    assert "奖金" in terms
    # Alias-expanded synonym words must also be present
    assert "竞赛" in terms
    assert "奖励" in terms


def test_extract_query_terms_includes_alias_synonym_for_store() -> None:
    """店铺 → 门店 alias expansion must surface 门店 as a query term."""
    terms = extract_query_terms("店铺入驻门槛")
    assert "店铺" in terms
    assert "门店" in terms


def test_alias_synonym_credit_only_when_candidate_contains_synonym() -> None:
    """A candidate that contains the synonym (竞赛) but not the original
    colloquial word (比赛) must receive enough term coverage to clear the
    gate when it carries the ``alias_fts_match`` flag (set by
    ``_retrieve_candidates`` when the candidate was surfaced via an alias
    expansion). Without the flag, the candidate must still be rejected so
    the alias terms alone cannot inflate an unrelated FTS hit.
    """
    # Candidate whose title contains the synonym (劳动竞赛奖励办法) but not
    # the original colloquial word (比赛). The alias_fts_match flag is set by
    # _retrieve_candidates when the candidate was retrieved via an alias variant.
    synonym_candidate = {
        "knowledge_id": "syn-1",
        "title": "劳动竞赛奖励办法",
        "text": "本办法规范劳动竞赛奖励的发放。",
        "score": 0.05,
        "alias_fts_match": True,
    }
    decision = evaluate_evidence(
        "比赛的奖金是多少",
        [synonym_candidate],
        threshold=0.35,
    )
    assert decision["accept"] is True, (
        "synonym-matched candidate with alias_fts_match must clear the gate "
        f"(final_relevance_score={decision.get('items', [{}])[0].get('final_relevance_score') if decision.get('items') else 'n/a'})"
    )


def test_alias_synonym_does_not_inflate_unrelated_candidate() -> None:
    """A candidate that contains neither the original word nor its synonym
    must NOT be inflated by the alias expansion, even with alias_fts_match.
    This guards against the alias terms leaking into unrelated evidence
    decisions.
    """
    unrelated_candidate = {
        "knowledge_id": "unr-1",
        "title": "差旅费管理办法",
        "text": "本办法规范差旅费报销标准。",
        "score": 0.05,
        "alias_fts_match": True,
    }
    decision = evaluate_evidence(
        "比赛的奖金是多少",
        [unrelated_candidate],
        threshold=0.35,
    )
    assert decision["accept"] is False


def test_alias_synonym_without_flag_still_rejected() -> None:
    """A synonym-matched candidate WITHOUT the ``alias_fts_match`` flag must
    NOT be inflated by the alias boost. This ensures the boost only fires
    when ``_retrieve_candidates`` verified the candidate via an alias
    expansion, not for arbitrary candidates that happen to contain a synonym.
    """
    synonym_candidate_no_flag = {
        "knowledge_id": "syn-2",
        "title": "劳动竞赛奖励办法",
        "text": "本办法规范劳动竞赛奖励的发放。",
        "score": 0.05,
        # NOTE: no alias_fts_match flag
    }
    decision = evaluate_evidence(
        "比赛的奖金是多少",
        [synonym_candidate_no_flag],
        threshold=0.35,
    )
    assert decision["accept"] is False


# --- SPEC Phase 3.3: core-term title boost for colloquial queries ------------


def test_core_term_title_boost_accepts_colloquial_query_with_regulation_doc() -> None:
    """A colloquial query like "线上店铺入驻门槛" must be accepted when the
    candidate title contains 3+ distinct 2-char query terms (公司/线上/合作)
    AND a regulation suffix (办法). Without this boost, n-gram dilution
    (60+ terms) keeps query_term_coverage below 0.2 and the generic-overlap
    penalty caps the score at 0.30, rejecting the correct document.
    """
    candidate = {
        "knowledge_id": "core-1",
        "title": "关于印发中国电信广西公司线上合作管理办法的通知",
        "text": "本办法规范线上合作的管理。",
        "score": 0.05,
    }
    decision = evaluate_evidence(
        "公司和外部商家合作卖东西的线上店铺入驻门槛",
        [candidate],
        threshold=0.35,
    )
    assert decision["accept"] is True
    assert decision["top_score"] >= 0.42


def test_core_term_title_boost_does_not_fire_without_regulation_suffix() -> None:
    """The boost must NOT fire when the title lacks a regulation suffix
    (办法/规定/制度/通知/规范), even if 3+ 2-char terms match. This prevents
    inflating unrelated candidates that happen to share generic words.
    """
    candidate = {
        "knowledge_id": "core-2",
        "title": "公司线上合作季度报告",  # no regulation suffix
        "text": "本季度公司线上合作情况。",
        "score": 0.05,
    }
    decision = evaluate_evidence(
        "公司和外部商家合作卖东西的线上店铺入驻门槛",
        [candidate],
        threshold=0.35,
    )
    assert decision["accept"] is False


def test_core_term_title_boost_does_not_fire_with_only_2_terms() -> None:
    """The boost must NOT fire when fewer than 3 distinct 2-char query terms
    appear in the title. Two-term overlap is not strong enough evidence.
    """
    candidate = {
        "knowledge_id": "core-3",
        "title": "线上合作管理办法",
        "text": "本办法规范线上合作的管理。",
        "score": 0.05,
    }
    # Query has 公司/线上/合作, but title only has 线上/合作 (2 terms, not 3)
    # Wait — "公司" is also in "公司线上合作" but the title is just "线上合作管理办法"
    # which does NOT contain "公司". So only 2 terms match.
    decision = evaluate_evidence(
        "公司和外部商家合作卖东西的线上店铺入驻门槛",
        [candidate],
        threshold=0.35,
    )
    assert decision["accept"] is False


def test_generic_overlap_penalty_still_applies_without_core_term_boost() -> None:
    """The generic-overlap penalty (cap at 0.30) must still apply when the
    core-term title boost does NOT fire. This ensures that candidates with
    weak title overlap are still rejected even if they have some FTS hits.
    """
    # Title has only 1 matching 2-char term and no regulation suffix.
    candidate = {
        "knowledge_id": "core-4",
        "title": "季度营收报告",
        "text": "本季度营收情况说明。",
        "score": 0.05,
    }
    decision = evaluate_evidence(
        "公司和外部商家合作卖东西的线上店铺入驻门槛",
        [candidate],
        threshold=0.35,
    )
    assert decision["accept"] is False


# --- SPEC Phase 3.3: ranking_reason auditability ---------------------------

def test_score_candidate_relevance_returns_ranking_reason_with_boosts() -> None:
    """score_candidate_relevance must return a structured ranking_reason
    that records which boosts/penalties fired, for snapshot auditability."""
    from src.services.relevance_gate import score_candidate_relevance

    candidate = {
        "knowledge_id": "rr-1",
        "title": "关于印发中国电信广西公司线上合作管理办法的通知",
        "text": "本办法规范线上合作的管理。",
        "score": 0.05,
    }
    scores = score_candidate_relevance(
        "公司和外部商家合作卖东西的线上店铺入驻门槛",
        candidate,
    )
    rr = scores.get("ranking_reason")
    assert isinstance(rr, dict)
    assert "primary_signal" in rr
    assert "boosts" in rr and isinstance(rr["boosts"], list)
    assert "penalties" in rr and isinstance(rr["penalties"], list)
    assert "scope_reason" in rr
    assert "regulation_phrase_reason" in rr
    assert "intent" in rr
    # The core-term title boost must have fired for this candidate.
    assert "core_term_title_boost" in rr["boosts"]
    assert rr["core_term_title_boosted"] is True


def test_ranking_reason_records_alias_fts_match_boost() -> None:
    """An alias-matched candidate must record alias_fts_match in boosts."""
    from src.services.relevance_gate import score_candidate_relevance

    candidate = {
        "knowledge_id": "rr-2",
        "title": "劳动竞赛管理办法",
        "text": "本办法规范劳动竞赛与奖励。",
        "alias_fts_match": True,
        "rerank_score": 0.85,
    }
    scores = score_candidate_relevance("比赛奖金多少", candidate)
    rr = scores["ranking_reason"]
    assert rr["alias_fts_match"] is True
    assert "alias_fts_match" in rr["boosts"]
    assert "reranker_high_confidence" in rr["boosts"]
    assert rr["rerank_score"] == 0.85


def test_ranking_reason_records_penalties_for_live_external() -> None:
    """A live_external query must record the live_external_cap penalty."""
    from src.services.relevance_gate import score_candidate_relevance

    candidate = {
        "knowledge_id": "rr-3",
        "title": "今日股价行情通报",
        "text": "今日股价行情。",
        "score": 0.05,
    }
    scores = score_candidate_relevance("今天的实时股价行情", candidate)
    rr = scores["ranking_reason"]
    assert rr["intent"] == "live_external"
    assert "live_external_cap" in rr["penalties"]


def test_canonical_snapshot_carries_ranking_reasons_summary() -> None:
    """build_canonical_snapshot must surface ranking_reasons on accepted
    items and in stages.ranking_reasons for per-case auditability."""
    from src.retrieval.canonical_snapshot import build_canonical_snapshot
    from src.services.relevance_gate import apply_relevance_scores

    candidates = [
        {
            "knowledge_id": "snap-1",
            "title": "关于印发中国电信广西公司线上合作管理办法的通知",
            "text": "本办法规范线上合作的管理。",
            "score": 0.05,
            "passage_id": "p1",
        },
    ]
    # Score candidates first so ranking_reason is attached to each item.
    apply_relevance_scores("线上合作管理办法", candidates)
    snapshot = build_canonical_snapshot(
        "线上合作管理办法",
        candidates,
        threshold=0.35,
        top_k=5,
    )
    # Top-level ranking_reasons summary list.
    assert "ranking_reasons" in snapshot
    assert isinstance(snapshot["ranking_reasons"], list)
    assert len(snapshot["ranking_reasons"]) >= 1
    # stages.ranking_reasons mirror.
    assert "ranking_reasons" in snapshot["stages"]
    # Each accepted item carries ranking_reason at top level.
    for item in snapshot["accepted_items"]:
        assert "ranking_reason" in item
        assert isinstance(item["ranking_reason"], dict)
        assert "primary_signal" in item["ranking_reason"]

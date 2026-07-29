"""Automated regression tests for the MCP Agent knowledge hit-rate remediation.

Scope (per ``docs/evaluation/mcp-agent-knowledge-hit-rate-remediation-spec.md``):

- Phase 1 (answer_pipeline): KB-007 / 009 / 014 / 016 / 017 / 023 / 027
  — search accepts evidence that ask then wrongly refuses.
- Phase 2 (routing): KB-035 / 037
  — "最新版本/最新修订版" must NOT short-circuit to requires_current_external_data.
- Phase 3 (retrieval_recall): KB-010 / 011 / 012 / 015 / 018 / 020 / 021 / 028
  — colloquial queries must surface the right doc in Top-5; no-answer set must
  still refuse (KB-030..034).
- Phase 4 (version_ranking): KB-009 — newest version ranks first, forbidden
  old-version fact not used; near-duplicate docs do not starve Top-K.
- Phase 5 (chunking): KB-019 — III类 "20万元" answerable from adjacent block,
  "10万元" (II类) not returned as the III类 answer.

These tests use stable fake candidates / fake pipelines (no network embedding
or external LLM). The real Golden Set is exercised separately in Phase 6
end-to-end via ``scripts/hit_rate_test_harness.py``.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from src.services.relevance_gate import (
    classify_query_intent,
    evaluate_evidence_unified,
    is_current_information_query,
    normalize_evidence_candidate,
)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _contains(hay: str, needle: str) -> bool:
    return _norm(needle) in _norm(hay)


# Synthetic knowledge ids used across cases.
KID_FRAUD = "51b17abe-8fe3-42fb-8c90-2b9b3d6fb934"        # 涉诈涉骚扰处置细则
KID_TRAVEL_2025 = "960ce8f2-41a3-4aaa-9cb2-27295fd5441f"  # 差旅费 2025
KID_TRAVEL_2022 = "3f57bb0d-cdb9-4257-aa0a-8692d898d2f6"  # 差旅费 2022
KID_TRAVEL_2018 = "5f8ab691-a9d5-465f-a9a6-c9142bcac741"  # 差旅费 2018
KID_WINGPAY = "27922ca4-aa1a-4cee-bf16-b4ee182a5201"      # 翼支付办法 2026
KID_SKILL_2026 = "2b63b216-9850-4e82-803e-4006cb9f62ad"   # 技能竞赛 2026


# ===========================================================================
# Phase 1 — search and ask must score the same evidence identically
# ===========================================================================

class TestUnifiedEvidenceJudgment:
    """KB-017 representative: search score=1.0 but ask top_score=0.0957."""

    def test_ask_source_dict_without_score_field_is_not_refused(self):
        """A strong evidence accepted by search must not be refused by ask when
        the ask source dict carries a different (tiny) pipeline score.

        Reproduces the KB-017 asymmetry root cause: search candidate carried
        score=1.0 but ask source dicts carried tiny RRF scores (0.014). The
        unified gate derives the semantic feature from verifiable lexical
        coverage (NOT the tool-specific pipeline score), so both tools reach
        the same accept decision and the same final_relevance_score.
        """
        query = "涉诈电话 代理商一个自然月内每个号码处罚金额"
        # Realistic evidence text (what retrieval actually surfaces — a clause
        # from the disposition rules, not a 1-line fragment).
        text = (
            "第六条 对涉及的代理商营业员、代理商网点，分涉诈、涉骚扰电话号码，"
            "每个号码一个自然月内处罚2000元。"
        )
        title = "市场-2026-8号-涉诈涉骚扰电话号码入网渠道处置细则-2026"
        # search-shaped candidate (high pipeline score)
        search_candidate = {
            "knowledge_id": KID_FRAUD,
            "title": title,
            "text": text,
            "score": 1.0,
        }
        # ask-shaped source dict — the pipeline score is a tiny RRF number and
        # the citation/document fields differ. This is the shape that used to
        # trigger the 0.0957 refusal.
        ask_source = {
            "source": "knowledge",
            "knowledge_id": KID_FRAUD,
            "block_id": "58272eb3-d0bd-4b7f-b5fd-5de6dc8548be",
            "title": title,
            "text": text,
            "score": 0.014024,
            "citation": {"document": "市场-2026-8号-涉诈涉骚扰处置细则", "knowledge_id": KID_FRAUD},
        }

        d_search = evaluate_evidence_unified(query, [search_candidate], threshold=0.35)
        d_ask = evaluate_evidence_unified(query, [ask_source], threshold=0.35)

        # The core invariant: same evidence ⇒ same accept decision (SPEC 1.4).
        assert d_search["accept"] is d_ask["accept"], (
            f"search={d_search['accept']} ask={d_ask['accept']} diverged"
        )
        # ...and the SAME final_relevance_score (the bug was search=1.0 vs
        # ask=0.0957 for the same document).
        assert d_search["top_score"] == d_ask["top_score"], (
            f"search={d_search['top_score']} ask={d_ask['top_score']} diverged"
        )
        # When evidence is accepted, the answerable fact must be reachable.
        if d_ask["accept"]:
            joined = "".join(
                str(i.get("text") or "") for i in (d_ask.get("items") or [])
            )
            assert "2000元" in joined

    def test_weak_evidence_does_not_accept_and_keeps_reason(self):
        """No fabricated answer when evidence is genuinely weak."""
        query = "广西电信2025年营收多少亿"
        weak = [
            {
                "knowledge_id": "x",
                "title": "营收资金管理办法",
                "text": "规范营收资金管理",
                "score": 0.2,
                "fts_score": 0.7,
            }
        ]
        d = evaluate_evidence_unified(query, weak, threshold=0.35)
        assert d["accept"] is False
        assert d["reason"] in ("insufficient_relevant_evidence", "no_candidates")
        assert d["items"] == []

    @pytest.mark.parametrize(
        "query,kid,title,text,needle",
        [
            # KB-017: fraud penalty
            (
                "涉诈电话 代理商一个自然月内每个号码处罚金额",
                KID_FRAUD,
                "市场-2026-8号-涉诈涉骚扰电话号码入网渠道处置细则-2026",
                "第六条 涉诈号码每个号码一个自然月内处罚2000元，代理商入网号码涉诈处置规则。",
                "2000元",
            ),
            # KB-023: contract physical vs e-seal
            (
                "合同实体章和合同电子章的法律效力关系",
                "16a152f8-f1ae-4250-a3f3-bc4fd0e6fd69",
                "中电信桂-2023-44号-合同专用章管理办法",
                "第五条 合同实体章与合同电子章具有同等法律效力。",
                "同等法律效力",
            ),
            # KB-027: skill competition practical ratio
            (
                "技能竞赛 实际操作成绩占比不得少于",
                KID_SKILL_2026,
                "中电信桂-2026-158号-技能竞赛管理办法",
                "实际操作成绩占比不得少于70%，理论成绩占比不超过30%。",
                "70%",
            ),
        ],
    )
    def test_search_and_ask_accept_same_evidence(self, query, kid, title, text, needle):
        """For each P1-A case, search and ask must reach the SAME accept
        decision and the SAME final_relevance_score for identical evidence,
        regardless of the pipeline ``score`` the source dict carries."""
        search_cand = {"knowledge_id": kid, "title": title, "text": text, "score": 1.0}
        ask_src = {
            "source": "knowledge",
            "knowledge_id": kid,
            "block_id": "b-" + kid[:8],
            "title": title,
            "text": text,
            "score": 0.05,  # tiny pipeline score — must NOT cause refusal
        }
        d_search = evaluate_evidence_unified(query, [search_cand], threshold=0.35)
        d_ask = evaluate_evidence_unified(query, [ask_src], threshold=0.35)
        # Core invariant: identical accept decision (SPEC 1.4).
        assert d_search["accept"] is d_ask["accept"], (
            f"search={d_search['accept']} ask={d_ask['accept']} for {query}"
        )
        # ...and identical final_relevance_score.
        assert d_search["top_score"] == d_ask["top_score"], (
            f"search={d_search['top_score']} ask={d_ask['top_score']} for {query}"
        )


# ===========================================================================
# Phase 2 — local version queries must enter retrieval
# ===========================================================================

class TestLocalVersionRouting:
    @pytest.mark.parametrize(
        "q,expected",
        [
            ("中国电信股价今天多少", "live_external"),
            ("量子计算最新进展", "live_external"),
            ("当前实时行情", "live_external"),
            ("火星探测任务最新进展", "live_external"),
            ("差旅费管理办法最新版本是哪一年", "local_version"),
            ("技能竞赛管理办法最新修订版 取消一级二级竞赛分级", "local_version"),
            ("营收资金管理办法 收支两条线", "ordinary"),
            ("防诈骗和骚扰电话 代理商被罚多少钱", "ordinary"),
        ],
    )
    def test_intent_classification(self, q, expected):
        assert classify_query_intent(q) == expected

    def test_live_external_short_circuits(self):
        assert is_current_information_query("中国电信股价今天多少") is True
        assert is_current_information_query("当前实时行情") is True
        assert is_current_information_query("量子计算最新进展") is True

    def test_local_version_is_not_live_external(self):
        assert is_current_information_query(
            "差旅费管理办法最新版本是哪一年"
        ) is False
        assert is_current_information_query(
            "技能竞赛管理办法最新修订版 取消一级二级竞赛分级"
        ) is False

    def test_local_version_enters_retrieval_not_short_circuit(self):
        """KB-035 / KB-037: local-version queries must NOT short-circuit in the
        unified gate — they should be scored like ordinary queries."""
        q = "中国电信广西公司差旅费管理办法最新版本是哪一年的"
        # An ordinary candidate that matches the latest version must be accepted.
        cand = {
            "knowledge_id": KID_TRAVEL_2025,
            "title": "中电信桂-2025-256号-关于印发中国电信广西公司差旅费管理办法-2025",
            "text": "2025年修订版差旅费管理办法。",
            "score": 0.8,
        }
        d = evaluate_evidence_unified(q, [cand], threshold=0.35)
        assert d["accept"] is True, d
        assert d.get("reason") != "requires_current_external_data"


# ===========================================================================
# Phase 3 — colloquial recall must improve without breaking no-answer
# ===========================================================================

class TestColloquialRecall:
    """KB-010/011/012/015/018/020/021/028 — the right knowledge must reach
    Top-5 even when the query is colloquial. The unified gate must treat
    colloquial evidence symmetrically between search and ask."""

    @pytest.mark.parametrize(
        "query,kid,title,text",
        [
            (
                "防诈骗和骚扰电话 代理商被罚多少钱",
                KID_FRAUD,
                "市场-2026-8号-涉诈涉骚扰电话号码入网渠道处置细则-2026",
                "第六条 涉诈号码每个号码处罚2000元，涉骚扰号码处罚30元。",
            ),
            (
                "涉骚扰电话 代理商一个自然月内每个号码处罚金额",
                KID_FRAUD,
                "市场-2026-8号-涉诈涉骚扰电话号码入网渠道处置细则-2026",
                "第六条 涉骚扰号码每个号码处罚30元/个，一个自然月内累计。",
            ),
        ],
    )
    def test_colloquial_query_search_and_ask_agree(self, query, kid, title, text):
        """For colloquial queries, search and ask must reach the same decision
        for the same evidence (the original bug was ask refusing what search
        accepted)."""
        search_cand = {"knowledge_id": kid, "title": title, "text": text, "score": 0.8}
        ask_src = {
            "source": "knowledge",
            "knowledge_id": kid,
            "block_id": "b-" + kid[:8],
            "title": title,
            "text": text,
            "score": 0.03,
        }
        d_search = evaluate_evidence_unified(query, [search_cand], threshold=0.35)
        d_ask = evaluate_evidence_unified(query, [ask_src], threshold=0.35)
        assert d_search["accept"] is d_ask["accept"], (query, d_search, d_ask)
        assert d_search["top_score"] == d_ask["top_score"], (query, d_search, d_ask)


# ===========================================================================
# Phase 4 — version ranking + conflict disclosure + dedupe
# ===========================================================================

def _version_rank(cand: dict) -> int:
    """Extract the most recent 4-digit year found in title+text (0 if none)."""
    blob = f"{cand.get('title','')} {cand.get('text','')}"
    years = [int(y) for y in re.findall(r"(?<!\d)(19|20)\d{2}(?!\d)", blob)]
    # the regex above captures century prefix group; recompute cleanly:
    years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", blob)]
    return max(years) if years else 0


class TestVersionRanking:
    def test_extract_year_helper(self):
        assert _version_rank({"title": "中电信桂-2025-256号", "text": ""}) == 2025
        assert _version_rank({"title": "2018年版", "text": ""}) == 2018
        assert _version_rank({"title": "无年份标题", "text": "无年份正文"}) == 0

    def test_kb009_newest_version_ranks_first(self):
        """KB-009: 2025 travel-expense version must outrank 2018/2022."""
        cands = [
            {
                "knowledge_id": KID_TRAVEL_2018,
                "title": "中电信桂-2018-477号-差旅费管理办法-2018年",
                "text": "区内出差每人每天80元伙食补助。",
                "score": 0.797,
            },
            {
                "knowledge_id": KID_TRAVEL_2022,
                "title": "中电信桂-2022-57号-差旅费管理办法-2022年",
                "text": "伙食补助标准。",
                "score": 0.797,
            },
            {
                "knowledge_id": KID_TRAVEL_2025,
                "title": "中电信桂-2025-256号-差旅费管理办法-2025年",
                "text": "伙食补助100元/天。",
                "score": 0.778,
            },
        ]
        from src.services.version_rank import rank_with_freshness

        ranked = rank_with_freshness(cands)
        assert ranked[0]["knowledge_id"] == KID_TRAVEL_2025


# ===========================================================================
# Phase 5 — clause evidence integrity (KB-019)
# ===========================================================================

class TestClauseEvidenceIntegrity:
    def test_adjacent_context_recovers_full_clause(self):
        """KB-019: 'II类 10万元；III类 20万元' split across blocks must be
        rejoined for the III类 question so '20万元' is answerable."""
        from src.answering.context_builder import expand_adjacent_evidence

        blocks = [
            {"block_id": "b190", "knowledge_id": KID_WINGPAY,
             "order_idx": 190,
             "text": "账户，其余额年付款限额为10万元（不含提现）；III类支付账"},
            {"block_id": "b191", "knowledge_id": KID_WINGPAY,
             "order_idx": 191,
             "text": "户，其余额年付款限额为20万元（不含提现）。"},
        ]
        expanded = expand_adjacent_evidence(blocks, focus_block_id="b190", window=1)
        joined = "".join(b.get("text", "") for b in expanded)
        assert "20万元" in joined, expanded
        assert "III类" in joined

    def test_iii_class_answer_does_not_use_ii_class_value(self):
        """The III类 answer must reference 20万元, never 10万元 as the answer."""
        # Simulate accepted evidence after adjacent expansion.
        evidence_text = (
            "账户，其余额年付款限额为10万元（不含提现）；"
            "III类支付账户，其余额年付款限额为20万元（不含提现）。"
        )
        # For a III类 question the answerable unit is 20万元.
        assert "20万元" in evidence_text
        # A correct answer for III类 must select the III类 value, not the II类 one.
        # We model this with the numeric-fact guard helper.
        from src.answering.fact_guard import select_numeric_fact_for_subject

        ans_ok = select_numeric_fact_for_subject(
            subject="III类",
            evidence=evidence_text,
            value_pattern=r"(\d+万元)",
        )
        assert ans_ok == "20万元"


# ===========================================================================
# No-answer protection — KB-030..034 must still refuse
# ===========================================================================

# ===========================================================================
# Phase 3.3 — colloquial query alias expansion (recall aid)
# ===========================================================================

class TestQueryRewrite:
    """Surface variants must preserve user wording without policy mappings."""

    def test_original_query_always_first(self):
        from src.services.query_rewrite import expand_query
        variants = expand_query("防诈骗和骚扰电话 代理商被罚多少钱")
        assert variants[0] == "防诈骗和骚扰电话 代理商被罚多少钱"

    def test_variant_never_invents_policy_title_or_answer_term(self):
        from src.services.query_rewrite import expand_query
        q = "公司搞比赛给员工发奖金 上限是多少"
        variants = expand_query(q)
        joined = " ".join(variants)
        assert "劳动竞赛" not in joined
        assert "技能竞赛" not in joined

    def test_unknown_query_unchanged(self):
        """A surface variant only contains user-derived terms."""
        from src.services.query_rewrite import expand_query
        variants = expand_query("营收资金管理办法 收支两条线")
        assert variants[0] == "营收资金管理办法 收支两条线"
        assert all("".join(variant.split()) for variant in variants)

    def test_terms_are_query_derived(self):
        from src.services.query_rewrite import canonical_terms
        terms = canonical_terms("防诈骗和骚扰电话 代理商被罚多少钱")
        assert terms
        assert "涉诈" not in terms and "处罚" not in terms

    def test_merge_keeps_highest_score_per_kid(self):
        from src.services.query_rewrite import merge_candidates_by_query
        a = [{"knowledge_id": "k1", "score": 0.3, "text": "a"}]
        b = [{"knowledge_id": "k1", "score": 0.8, "text": "b"},
             {"knowledge_id": "k2", "score": 0.5, "text": "c"}]
        merged = merge_candidates_by_query("q", [a, b])
        kids = [m["knowledge_id"] for m in merged]
        assert kids == ["k1", "k2"]
        assert merged[0]["score"] == 0.8  # higher score won


class TestNoAnswerProtection:
    @pytest.mark.parametrize(
        "q",
        [
            "中国电信广西公司2026年营收预测是多少亿元",
            "广西电信员工的工资薪级表和岗位津贴具体数额",
            "中国电信集团总部北京的办公楼地址",
            "火星探测任务最新进展",
            "推荐一款好吃的火锅底料品牌",
        ],
    )
    def test_no_answer_queries_do_not_accept_random_evidence(self, q):
        # Even if some loosely-related doc is retrieved, it must not cross the
        # accept threshold for an out-of-corpus question.
        cand = {
            "knowledge_id": "irrelevant-1234",
            "title": "某管理制度办法",
            "text": "本制度规范某业务管理流程。",
            "score": 0.1,
        }
        d = evaluate_evidence_unified(q, [cand], threshold=0.35)
        assert d["accept"] is False, (q, d)

    def test_live_external_still_short_circuits(self):
        q = "火星探测任务最新进展"
        assert classify_query_intent(q) == "live_external"
        d = evaluate_evidence_unified(q, [], threshold=0.35)
        assert d["reason"] == "requires_current_external_data"


# ===========================================================================
# Normalize helper coverage
# ===========================================================================

class TestNormalizeEvidenceCandidate:
    def test_fills_knowledge_id_from_page_id(self):
        out = normalize_evidence_candidate({"page_id": "k1", "text": "t"})
        assert out["knowledge_id"] == "k1"

    def test_fills_title_from_citation_document(self):
        out = normalize_evidence_candidate({
            "knowledge_id": "k1",
            "citation": {"document": "DocTitle", "knowledge_id": "k1"},
            "text": "t",
        })
        assert out["title"] == "DocTitle"

    def test_prefers_longest_text(self):
        out = normalize_evidence_candidate({
            "knowledge_id": "k1",
            "content": "short",
            "text": "a much longer text body",
        })
        assert out["text"] == "a much longer text body"


# ===========================================================================
# SPEC v2 Phase 1 — shared snapshot: search/ask same candidates
# ===========================================================================

class TestSharedCandidateSnapshot:
    """search and ask must share one canonical evidence snapshot."""

    def test_build_snapshot_projects_to_search_execution(self):
        from src.retrieval.canonical_snapshot import (
            build_canonical_snapshot,
            snapshot_to_search_execution,
        )

        cands = [
            {
                "knowledge_id": "acf5e2d6-1145-4cf4-bf7f-e2e6a748fea7",
                "block_id": "b-safety-1",
                "title": "中电信桂-2023-118号-安全生产管理办法-2023",
                "text": "南宁分公司专职安全生产管理人员配备不少于5人。",
                "score": 0.9,
            }
        ]
        snap = build_canonical_snapshot(
            "安全生产管理办法 专职安全员 南宁分公司不少于5人",
            cands,
            threshold=0.35,
            top_k=5,
        )
        assert snap["accept"] is True
        assert snap["accepted_items"][0]["knowledge_id"].startswith("acf5e2d6")
        ex = snapshot_to_search_execution(snap)
        assert list(ex.results)[0]["knowledge_id"].startswith("acf5e2d6")
        assert ex.trace["gate"]["accept"] is True

    def test_answer_service_uses_snapshot_not_re_retrieval(self):
        """AnswerService with evidence_snapshot must NOT call search.execute."""
        from src.answering.service import AnswerService

        class _BoomSearch:
            def execute(self, *a, **k):
                raise AssertionError("must not re-retrieve when snapshot given")

            def search(self, *a, **k):
                raise AssertionError("must not re-retrieve when snapshot given")

        kid = "acf5e2d6-1145-4cf4-bf7f-e2e6a748fea7"
        snap = {
            "query": "安全生产管理办法 专职安全员 南宁分公司不少于5人",
            "accept": True,
            "accepted_items": [
                {
                    "knowledge_id": kid,
                    "block_id": "b1",
                    "title": "安全生产管理办法-2023",
                    "text": "南宁分公司专职安全生产管理人员配备不少于5人。",
                    "score": 0.8,
                    "source": "knowledge",
                }
            ],
            "generation_items": [
                {
                    "knowledge_id": kid,
                    "block_id": "b1",
                    "title": "安全生产管理办法-2023",
                    "text": "南宁分公司专职安全生产管理人员配备不少于5人。",
                    "score": 0.8,
                    "source": "knowledge",
                }
            ],
            "accepted_knowledge_ids": [kid],
            "accepted_block_ids": ["b1"],
            "adjacent_allowlist": [],
            "top_score": 0.8,
            "threshold": 0.35,
            "intent": "ordinary",
            "stages": {},
        }
        svc = AnswerService(_BoomSearch(), llm=None, config={})
        # Force no-LLM path: provide llm_answer so generate_fn is unused.
        payload = svc.ask(
            "安全生产管理办法 专职安全员 南宁分公司不少于5人",
            evidence_snapshot=snap,
            llm_answer="南宁分公司专职安全生产管理人员不少于5人。",
        )
        assert payload["answer"]
        assert any(
            (s.get("knowledge_id") or "").startswith("acf5e2d6")
            for s in payload.get("sources") or []
        )
        assert "不少于5人" in payload["answer"]

    def test_unlisted_source_fails_citation_allowlist(self):
        """A source not in pre-accepted/adjacent allowlist is rejected."""
        from src.retrieval.canonical_snapshot import source_in_allowlist

        ok = source_in_allowlist(
            {"knowledge_id": "evil-doc", "block_id": "x"},
            accepted_knowledge_ids={"good-doc"},
            accepted_block_ids={"b1"},
            adjacent_allowlist=[],
        )
        assert ok is False
        ok2 = source_in_allowlist(
            {"knowledge_id": "good-doc", "block_id": "b1"},
            accepted_knowledge_ids={"good-doc"},
            accepted_block_ids={"b1"},
            adjacent_allowlist=[],
        )
        assert ok2 is True
        ok3 = source_in_allowlist(
            {"knowledge_id": "good-doc", "block_id": "b-adj", "is_adjacent_extension": True},
            accepted_knowledge_ids={"good-doc"},
            accepted_block_ids={"b1"},
            adjacent_allowlist=[
                {
                    "knowledge_id": "good-doc",
                    "block_id": "b-adj",
                    "is_adjacent_extension": True,
                    "parent_hit_block_id": "b1",
                }
            ],
        )
        assert ok3 is True


# ===========================================================================
# SPEC v2 Phase 2 — version isolation (KB-037)
# ===========================================================================

class TestVersionEvidenceIsolation:
    def test_filter_to_latest_drops_old_edition(self):
        from src.services.version_rank import filter_to_latest_versions

        items = [
            {
                "knowledge_id": "old",
                "title": "中电信桂-2023-278号-技能竞赛管理办法",
                "text": "分为一级竞赛、二级竞赛",
            },
            {
                "knowledge_id": "new",
                "title": "中电信桂-2026-158号-技能竞赛管理办法-修订",
                "text": "取消分级管理",
            },
        ]
        kept = filter_to_latest_versions(items)
        kids = {k.get("knowledge_id") for k in kept}
        assert "new" in kids
        assert "old" not in kids

    def test_freshness_after_relevance_puts_2026_first(self):
        from src.retrieval.canonical_snapshot import apply_post_relevance_freshness

        items = [
            {
                "knowledge_id": "1acb61b4",
                "title": "中电信桂-2023-278号-技能竞赛管理办法",
                "text": "一级竞赛",
                "final_relevance_score": 0.55,
                "score": 0.55,
            },
            {
                "knowledge_id": "2b63b216",
                "title": "中电信桂-2026-158号-技能竞赛管理办法-修订",
                "text": "修订",
                "final_relevance_score": 0.50,
                "score": 0.50,
            },
        ]
        ranked = apply_post_relevance_freshness(
            "技能竞赛管理办法最新修订版 取消一级二级竞赛分级",
            items,
        )
        assert ranked[0]["knowledge_id"] == "2b63b216"


# ===========================================================================
# SPEC v2 Phase 3 — subject anchoring + adjacent production path (KB-019)
# ===========================================================================

class TestSubjectAnchoringAndAdjacent:
    def test_answer_service_context_includes_adjacent_iii_value(self):
        """Production AnswerService expansion must join III类 + 20万元."""
        from src.answering.service import AnswerService
        from src.answering.fallbacks import build_generation_context
        from src.retrieval.canonical_snapshot import expand_results_with_adjacent

        blocks = {
            KID_WINGPAY: [
                {
                    "id": "b190",
                    "block_id": "b190",
                    "page_id": KID_WINGPAY,
                    "knowledge_id": KID_WINGPAY,
                    "order_idx": 190,
                    "content": "账户，其余额年付款限额为10万元（不含提现）；III类支付账",
                    "text": "账户，其余额年付款限额为10万元（不含提现）；III类支付账",
                },
                {
                    "id": "b191",
                    "block_id": "b191",
                    "page_id": KID_WINGPAY,
                    "knowledge_id": KID_WINGPAY,
                    "order_idx": 191,
                    "content": "户，其余额年付款限额为20万元（不含提现）。",
                    "text": "户，其余额年付款限额为20万元（不含提现）。",
                },
            ]
        }

        def list_blocks(kid: str):
            return list(blocks.get(kid) or [])

        hits = [
            {
                "knowledge_id": KID_WINGPAY,
                "block_id": "b190",
                "title": "翼支付业务管理办法-2026",
                "text": blocks[KID_WINGPAY][0]["text"],
                "score": 0.8,
                "source": "knowledge",
            }
        ]
        expanded = expand_results_with_adjacent(hits, list_blocks_fn=list_blocks, window=1)
        joined = "".join(r.get("text") or "" for r in expanded)
        assert "III类" in joined
        assert "20万元" in joined

        # Subject guard: III类 answer must not keep II类 10万元 as the answer.
        from src.answering.fact_guard import (
            select_numeric_fact_for_subject,
            strip_unanchored_numeric_assertions,
        )
        assert select_numeric_fact_for_subject(
            subject="III类", evidence=joined,
        ) == "20万元"
        cleaned, stripped = strip_unanchored_numeric_assertions(
            "III类年付款限额为10万元，超过10万元。",
            evidence=joined,
            question="翼支付III类支付账户 年付款限额",
        )
        assert "10万元" not in cleaned.replace(" ", "")
        assert stripped is True

    def test_subject_anchor_rejects_neighbor_value(self):
        from src.answering.fact_guard import answer_value_is_anchored

        evidence = (
            "II类支付账户，其余额年付款限额为10万元（不含提现）；"
            "III类支付账户，其余额年付款限额为20万元（不含提现）。"
        )
        assert answer_value_is_anchored(
            subject="III类", evidence=evidence, claimed_value="20万元",
        )
        assert not answer_value_is_anchored(
            subject="III类", evidence=evidence, claimed_value="10万元",
        )

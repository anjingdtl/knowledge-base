"""Phase 3.2: out-of-domain intent detection + financial-forecast live external.

These tests pin the new intent classifier patterns and the unified evidence
gate's short-circuit behavior. Each pattern that triggers a no-answer must:

  1. classify the query as ``out_of_domain`` (or ``live_external`` for forecasts);
  2. make ``evaluate_evidence_unified`` return ``accept=False`` with a distinct
     ``reason`` code;
  3. NOT fire on a structurally-similar but in-domain query (anti-overfit).
"""
from __future__ import annotations

from src.services.relevance_gate import (
    classify_query_intent,
    evaluate_evidence,
    evaluate_evidence_unified,
    is_current_information_query,
    out_of_domain_reason,
)


# --------------------------------------------------------------------------- #
# Out-of-domain consumer recommendations                                       #
# --------------------------------------------------------------------------- #


def test_consumer_recommendation_hotpot_classified_out_of_domain() -> None:
    """KB-034 regression: 推荐火锅底料 must not return corporate-policy hits."""
    q = "推荐一款好吃的火锅底料品牌"
    assert classify_query_intent(q) == "out_of_domain"
    assert out_of_domain_reason(q) == "consumer_recommendation"


def test_consumer_recommendation_restaurant_classified_out_of_domain() -> None:
    q = "推荐一家适合团建的餐厅"
    assert classify_query_intent(q) == "out_of_domain"
    assert out_of_domain_reason(q) == "consumer_recommendation"


def test_consumer_recommendation_movie_classified_out_of_domain() -> None:
    q = "推荐几部好看的电影"
    assert classify_query_intent(q) == "out_of_domain"
    assert out_of_domain_reason(q) == "consumer_recommendation"


# --------------------------------------------------------------------------- #
# Headquarters / office address lookups                                       #
# --------------------------------------------------------------------------- #


def test_hq_address_lookup_classified_out_of_domain() -> None:
    """KB-032 regression: 集团总部办公楼地址 is a public fact, not policy."""
    q = "中国电信集团总部北京的办公楼地址"
    assert classify_query_intent(q) == "out_of_domain"
    assert out_of_domain_reason(q) == "hq_address_lookup"


def test_hq_address_lookup_interrogative_first_classified_out_of_domain() -> None:
    q = "公司总部具体位置在哪里"
    assert classify_query_intent(q) == "out_of_domain"
    assert out_of_domain_reason(q) == "hq_address_lookup"


# --------------------------------------------------------------------------- #
# HR private salary data                                                       #
# --------------------------------------------------------------------------- #


def test_hr_salary_scale_classified_out_of_domain() -> None:
    """KB-031 regression: 工资薪级表 is HR private data, not policy."""
    q = "广西电信员工的工资薪级表和岗位津贴具体数额"
    assert classify_query_intent(q) == "out_of_domain"
    # The query matches both "工资薪级表" and "岗位津贴.{0,10}具体" — either is fine.
    assert out_of_domain_reason(q) == "hr_private_salary_data"


def test_hr_specific_pay_amount_classified_out_of_domain() -> None:
    q = "员工薪酬具体数额是多少"
    assert classify_query_intent(q) == "out_of_domain"
    assert out_of_domain_reason(q) == "hr_private_salary_data"


# --------------------------------------------------------------------------- #
# Financial / business forecasts (live_external extension)                    #
# --------------------------------------------------------------------------- #


def test_revenue_forecast_classified_live_external() -> None:
    """KB-030 regression: 2026年营收预测 is a future forecast, not policy."""
    q = "中国电信广西公司2026年营收预测是多少亿元"
    assert classify_query_intent(q) == "live_external"
    # is_current_information_query must return True so the MCP search tool's
    # first short-circuit fires (consistent with the no-answer envelope).
    assert is_current_information_query(q) is True


def test_profit_forecast_classified_live_external() -> None:
    q = "公司2025年净利润预计是多少"
    assert classify_query_intent(q) == "live_external"
    assert is_current_information_query(q) is True


def test_kpi_forecast_classified_live_external() -> None:
    q = "今年KPI预测达成情况"
    assert classify_query_intent(q) == "live_external"


# --------------------------------------------------------------------------- #
# Anti-overfit: structurally similar but in-domain queries must NOT trip      #
# --------------------------------------------------------------------------- #


def test_policy_query_with_recommend_verb_not_ood() -> None:
    """A policy query that happens to use 推荐 verb is still ordinary.

    e.g. "公司有哪些推荐的管理办法" — the noun after 推荐 is a policy noun
    (管理办法), not a consumer product. Must NOT be classified out_of_domain.
    """
    q = "公司有哪些推荐的管理办法"
    assert classify_query_intent(q) != "out_of_domain"


def test_address_in_policy_context_not_ood() -> None:
    """A policy query that mentions 地址 in a non-HQ context is still ordinary."""
    q = "差旅费管理办法规定的住宿地址范围"
    # "地址" appears but not co-located with 总部/办公楼 → no hq_address_lookup.
    assert classify_query_intent(q) != "out_of_domain"


def test_salary_in_policy_context_not_ood() -> None:
    """A policy query that mentions 津贴 in a policy sense is still ordinary.

    e.g. "差旅费管理办法规定的伙食补助标准" — this is a policy document
    question, not a request for HR private salary data.
    """
    q = "差旅费管理办法规定的伙食补助标准是多少"
    assert classify_query_intent(q) != "out_of_domain"


def test_ordinary_policy_query_unchanged() -> None:
    """A canonical ordinary policy query must still classify as ``ordinary``."""
    q = "营收资金管理办法 收支两条线"
    assert classify_query_intent(q) == "ordinary"


def test_local_version_query_unchanged() -> None:
    """Local-version queries must NOT be swept into out_of_domain or live_external."""
    q = "差旅费管理办法最新版本是哪一年的"
    assert classify_query_intent(q) == "local_version"


def test_financial_metric_without_forecast_verb_not_live() -> None:
    """A query that mentions 营收 but no forecast verb is still ordinary.

    e.g. "营收资金管理办法" is a regulation title, not a forecast question.
    """
    q = "营收资金管理办法 收支两条线"
    assert classify_query_intent(q) == "ordinary"


def test_forecast_verb_without_financial_metric_not_live() -> None:
    """A query that mentions 预计 but no financial metric is still ordinary.

    e.g. "差旅费办法预计实施时间" — asking about a planned effective date
    inside a policy, not a financial forecast. Must NOT be swept into
    live_external.
    """
    q = "差旅费办法预计实施时间是什么时候"
    assert classify_query_intent(q) == "ordinary"


# --------------------------------------------------------------------------- #
# Unified evidence gate short-circuits                                        #
# --------------------------------------------------------------------------- #


def test_evaluate_evidence_unified_rejects_out_of_domain() -> None:
    """evaluate_evidence_unified must return no_match for out_of_domain queries."""
    q = "推荐一款好吃的火锅底料品牌"
    decision = evaluate_evidence_unified(
        q,
        [{"knowledge_id": "k1", "title": "星级服务管理办法", "text": "..."}],
        threshold=0.35,
    )
    assert decision["accept"] is False
    assert decision["no_match"] is True
    assert decision["reason"].startswith("out_of_domain:")
    assert decision["intent"] == "out_of_domain"
    assert decision["items"] == []


def test_evaluate_evidence_unified_rejects_financial_forecast() -> None:
    """evaluate_evidence_unified must return no_match for financial forecasts."""
    q = "中国电信广西公司2026年营收预测是多少亿元"
    decision = evaluate_evidence_unified(
        q,
        [{"knowledge_id": "k1", "title": "营收资金管理办法", "text": "..."}],
        threshold=0.35,
    )
    assert decision["accept"] is False
    assert decision["no_match"] is True
    assert decision["reason"] == "requires_current_external_data"
    assert decision["intent"] == "live_external"


def test_evaluate_evidence_legacy_api_rejects_out_of_domain() -> None:
    """The older evaluate_evidence API must mirror the unified gate for OOD."""
    q = "广西电信员工的工资薪级表和岗位津贴具体数额"
    decision = evaluate_evidence(
        q,
        [{"knowledge_id": "k1", "title": "内控实施细则", "text": "..."}],
        threshold=0.35,
    )
    assert decision["accept"] is False
    assert decision["no_match"] is True
    assert decision["reason"].startswith("out_of_domain:")


def test_out_of_domain_reason_returns_none_for_ordinary() -> None:
    """out_of_domain_reason helper returns None for in-domain queries."""
    assert out_of_domain_reason("营收资金管理办法 收支两条线") is None
    assert out_of_domain_reason("") is None
    assert out_of_domain_reason("差旅费办法预计实施时间") is None

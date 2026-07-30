"""Unified relevance / no-answer gate for semantic, FTS, and hybrid hits.

FTS keyword hits alone are not sufficient evidence to answer a question.
"""
from __future__ import annotations

import re
from typing import Any

from src.services.numeric_unit_match import extract_number_units, score_numeric_unit_match

# --- Intent classification (SPEC Phase 2 / Phase 3.2) -----------------------
# A binary "最新 = live external" rule mis-routes local version queries to the
# no-answer short-circuit (KB-035/KB-037). We now split four intents:
#   * live_external   — needs real-time external data (today/live quotes/news,
#                       plus financial / business forecasts about future metrics)
#   * local_version   — compares versions inside the local corpus (最新版本/最新修订版/现行办法)
#   * out_of_domain   — clearly unrelated to the corporate-policy corpus
#                       (consumer recommendations, HQ address lookups,
#                       HR private salary data). SPEC Phase 3.2 §3.3.
#   * ordinary        — everything else (normal retrieval)
# Both ``live_external`` and ``out_of_domain`` short-circuit to no-answer with
# distinct reason codes. ``local_version`` proceeds to retrieval like ordinary.

# Strong live-data signals. These words almost always require an external,
# real-time source that a local policy corpus cannot provide.
_LIVE_EXTERNAL_STRONG_RE = re.compile(
    r"(今天|今日|实时|行情|股价|此刻|刚刚|当前市|实时行情|最新进展|最新动态|最新消息)",
)

# Financial / business forecast signals — future predictions about revenue,
# profit, income, or KPIs. The local policy corpus cannot authoritatively
# answer these. The match requires BOTH a financial metric noun AND a forecast
# verb, so a query like "差旅费办法预计实施时间" (asking about a planned
# effective date inside a policy) is NOT swept into live_external.
_FINANCIAL_METRIC_RE = re.compile(r"(营收|业绩|收入|利润|毛利|净利|营业额|销售额|KPI|指标)")
_FORECAST_VERB_RE = re.compile(r"(预测|预计|估计|预估|预报|展望|预期增长)")


def _is_financial_forecast_query(q: str) -> bool:
    """Return True when query asks for a financial/business forecast."""
    return bool(_FINANCIAL_METRIC_RE.search(q) and _FORECAST_VERB_RE.search(q))


# "最新进展/最新动态/最新消息" are news-style live queries even without a date
# word; keep them in the strong set so they still route to no-answer.
_LIVE_NEWS_RE = re.compile(r"(进展|动态|消息|行情|股价|实时)")

# Version-comparison intents that should be answered from the local corpus.
_LOCAL_VERSION_RE = re.compile(
    r"(最新版本|最新修订版|现行办法|现行制度|现行规定|现行版|最新文号|最新一版|当前版本)",
)
# Bare "最新" without a version noun is ambiguous — resolve by context.
_BARE_ZUIXIN_RE = re.compile(r"最新")
_VERSION_NOUN_RE = re.compile(r"(版本|修订版|文号|办法|制度|规定|版)")
# Local-version markers that often accompany "最新" (年份/哪一年/哪一版).
_VERSION_COMPARISON_HINT_RE = re.compile(r"(哪一年|哪一版|哪年版|什么时候|何时|几年|哪年的|版本号)")

# --- Out-of-domain signals (SPEC Phase 3.2 §3.3 no-answer/out-of-domain) ----
# Queries clearly unrelated to a corporate policy / regulation corpus. Each
# pattern must be tight enough that an ordinary policy query cannot trip it:
#   * consumer recommendations: must include "推荐" + a non-policy product noun;
#   * HQ / office address lookups: must include a location noun AND an address
#     interrogative — "总部地址在哪里" / "办公楼具体位置";
#   * HR private data: salary scales + "具体" / specific amount phrases.
# Each pattern is paired with a short reason code so audits can trace which
# signal fired without re-implementing the matcher.
_OUT_OF_DOMAIN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Consumer product / food / entertainment recommendations.
    (
        re.compile(r"推荐.{0,12}(火锅|餐厅|电影|游戏|书|音乐|歌曲|菜品|美食|零食|茶叶|酒)"),
        "consumer_recommendation",
    ),
    (
        re.compile(r"(有什么好吃|好吃的火锅|好吃的餐厅|好吃的菜|好喝的)"),
        "consumer_recommendation",
    ),
    # Headquarters / office address lookups (public fact, not policy).
    (
        re.compile(r"(总部|办公楼|办公地点|公司地址).{0,8}(地址|在哪|具体位置|位置|门牌)"),
        "hq_address_lookup",
    ),
    (
        re.compile(r"(地址|在哪|具体位置|位置).{0,8}(总部|办公楼|办公地点|公司地址)"),
        "hq_address_lookup",
    ),
    # HR private data: salary scales, specific pay amounts.
    (re.compile(r"工资薪级表"), "hr_private_salary_data"),
    (re.compile(r"岗位津贴.{0,10}具体"), "hr_private_salary_data"),
    (re.compile(r"薪酬.{0,10}具体数额"), "hr_private_salary_data"),
    (re.compile(r"具体数额.{0,12}(工资|薪酬|津贴|薪级|薪资)"), "hr_private_salary_data"),
]


def _classify_out_of_domain(q: str) -> str | None:
    """Return a reason code if ``q`` matches an out-of-domain pattern.

    Returns ``None`` when the query is in-domain (or in doubt — we never
    force-classify an ambiguous query as out_of_domain).
    """
    if not q:
        return None
    for pattern, reason in _OUT_OF_DOMAIN_PATTERNS:
        if pattern.search(q):
            return reason
    return None


def classify_query_intent(query: str) -> str:
    """Return one of: ``live_external`` / ``local_version`` / ``out_of_domain`` / ``ordinary``.

    Order matters:
      1. ``local_version`` first — a query that explicitly asks for a local
         document version (e.g. "差旅费管理办法最新版本是哪一年") must proceed
         to retrieval even if it contains a live-looking word.
      2. ``live_external`` next — strong live-data signals (today/quotes/news)
         AND financial forecasts (营收预测 / 2026年业绩预计) short-circuit to
         no-answer.
      3. ``out_of_domain`` — clearly unrelated to the corpus (consumer
         recommendations, HQ address, HR private salary data). Also short-circuits
         to no-answer with a distinct reason code.
      4. ``ordinary`` — everything else, proceeds to retrieval + scoring.
    """
    q = query or ""
    if not q.strip():
        return "ordinary"

    has_local_version = bool(_LOCAL_VERSION_RE.search(q))
    # "最新" + version noun + (year hint or bare version ask) ⇒ local version.
    if not has_local_version and _BARE_ZUIXIN_RE.search(q):
        has_version_noun = bool(_VERSION_NOUN_RE.search(q))
        has_comparison_hint = bool(_VERSION_COMPARISON_HINT_RE.search(q))
        # e.g. "最新修订版" / "最新版本" already covered; also "差旅费办法最新是哪一年的"
        if has_version_noun and has_comparison_hint:
            has_local_version = True

    if has_local_version:
        return "local_version"

    if _LIVE_EXTERNAL_STRONG_RE.search(q):
        return "live_external"

    # Bare "最新进展/最新动态/最新消息" ⇒ news-style live query.
    if _BARE_ZUIXIN_RE.search(q) and _LIVE_NEWS_RE.search(q):
        return "live_external"

    # Financial / business forecasts about future metrics — also live external.
    if _is_financial_forecast_query(q):
        return "live_external"

    # Out-of-domain patterns: consumer recs, HQ address, HR private data.
    if _classify_out_of_domain(q) is not None:
        return "out_of_domain"

    return "ordinary"


def out_of_domain_reason(query: str) -> str | None:
    """Return the matched out-of-domain reason code, or ``None``.

    Public helper so tests and snapshots can assert which pattern fired,
    without re-implementing the pattern list. The return value is suitable
    as a ``reason`` field on a no-answer envelope.
    """
    return _classify_out_of_domain(query or "")


def is_current_information_query(query: str) -> bool:
    """Backward-compatible boolean: True only for ``live_external`` intents.

    Local-version queries ("最新版本/最新修订版/现行办法") are NOT live external
    queries and must enter normal retrieval. Callers that previously relied on a
    True return to short-circuit should switch to
    :func:`classify_query_intent` == ``"live_external"``.
    """
    return classify_query_intent(query) == "live_external"


# Kept for reference / import compatibility; new code should use classify_query_intent.
_CURRENT_INFO_RE = _LIVE_EXTERNAL_STRONG_RE
_CJK_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_LATIN_TERM_RE = re.compile(r"[A-Za-z0-9]{2,}")


def extract_query_terms(query: str) -> set[str]:
    """Extract query terms for coverage scoring.

    Continuous Chinese runs are split into 2–4 character windows so a whole
    sentence is not treated as a single mega-term (which never matches titles).

    SPEC Phase 3.2: also include alias-expanded synonym words (e.g. 竞赛/门店
    for colloquial 比赛/店铺). When a candidate was surfaced by an alias-expanded
    FTS variant, the synonym word appears in the candidate title/text but not
    in the original query. Without including the synonym in the term set, the
    candidate's query_term_coverage stays low and the relevance gate rejects it
    even though the FTS channel verified the lexical match. The synonym terms
    are credited ONLY when the candidate actually contains them, so a generic
    no-answer candidate whose FTS match was incidental word overlap is still
    rejected.
    """
    q = query or ""
    terms: set[str] = set()
    for run in _CJK_TERM_RE.findall(q):
        if len(run) <= 4:
            terms.add(run)
            continue
        # Prefer multi-char windows (4, then 3, then 2) for institutional nouns
        for n in (4, 3, 2):
            for i in range(0, len(run) - n + 1):
                terms.add(run[i : i + n])
    terms |= {t.lower() for t in _LATIN_TERM_RE.findall(q)}
    # SPEC Phase 3.2: include alias-expanded synonym words so candidates
    # surfaced by alias FTS variants receive proper term coverage credit.
    # Lazy import to avoid a circular dependency at module load.
    try:
        from src.services.query_rewrite import build_alias_query_variants

        for v in build_alias_query_variants(q):
            src = v.get("source") or ""
            # source format: "alias:{original}→{synonym}"
            if src.startswith("alias:") and "→" in src:
                syn = src.split("→", 1)[1]
                for run in _CJK_TERM_RE.findall(syn):
                    if len(run) >= 2:
                        terms.add(run)
    except Exception:  # noqa: BLE001
        # Non-fatal: alias expansion is a recall aid, never a hard dependency.
        pass
    # Drop ultra-generic terms that alone never justify an answer
    stop = {
        "多少",
        "什么",
        "哪些",
        "怎么",
        "如何",
        "是否",
        "可以",
        "相关",
        "问题",
        "今天",
        "今日",
        "当前",
        "现在",
        "最新",
        "实时",
        "主题",
        "内容",
        "说明",
        "关于",
        "进行",
        "以及",
        "或者",
        "一个",
        "这个",
        "那个",
    }
    return {t for t in terms if t not in stop and len(t) >= 2}


def _candidate_text(item: dict) -> str:
    parts = [
        str(item.get("title") or ""),
        str(item.get("text") or item.get("content") or item.get("summary") or ""),
        str(item.get("chunk_text") or ""),
    ]
    return "\n".join(parts)


# --- Organizational scope alignment (SPEC Phase 3.1) -------------------------
# Branch-vs-HQ confusion is the single biggest wrong-family failure source in
# the development baseline.  The signal below is parsed from the *title* of a
# candidate (which already encodes the issuing org) and compared against the
# query's org scope.  It is intentionally a small boost or penalty so it can
# break near-ties without masking genuine relevance.

_BRANCH_TITLE_PATTERNS = (
    re.compile(r"号百"),
    re.compile(r"([\u4e00-\u9fff]{1,4}分公司)"),
    re.compile(r"([\u4e00-\u9fff]{1,4}子公司)"),
    re.compile(r"([\u4e00-\u9fff]{1,4}支公司)"),
    re.compile(r"([\u4e00-\u9fff]{1,4}营业部)"),
    re.compile(r"([\u4e00-\u9fff]{1,4}办事处)"),
)


def _extract_title_branch_tokens(title: str) -> list[str]:
    """Return branch tokens found in a candidate title.

    Used to tell HQ docs (empty list) from branch docs (non-empty) and to
    distinguish two different branches when both appear in the candidate pool.
    """
    if not title:
        return []
    found: list[str] = []
    for pat in _BRANCH_TITLE_PATTERNS:
        for m in pat.finditer(title):
            tok = m.group(0)
            if tok and tok not in found:
                found.append(tok)
    return found


def compute_scope_signal(query: str, title: str) -> tuple[float, str]:
    """Return ``(signal, reason)`` for org-scope alignment.

    The signal is a small additive modifier applied to the blended relevance
    score so it can break near-ties between HQ and branch candidates without
    masking genuine lexical evidence:

    * query-branch + title-branch (same branch)    → +0.15  (boost)
    * query-branch + title-branch (other branch)  → -0.10  (penalty)
    * query-branch + title-HQ                      → -0.20  (strong penalty)
    * query-HQ     + title-branch                   → -0.15  (penalty)
    * query-HQ     + title-HQ                       → +0.10  (boost)
    * query no-scope + title-branch                → -0.05  (mild penalty so
                                                          HQ docs outrank branch
                                                          docs at tied scores)
    * query no-scope + title-HQ                    →  0.00  (no change)

    Returns the signal as a float in ``[-0.20, +0.15]`` and a short reason
    code suitable for audit logging.
    """
    from src.answering.query_planner import extract_org_scope

    scope = extract_org_scope(query)
    title_branches = _extract_title_branch_tokens(title or "")
    is_title_branch = bool(title_branches)

    if scope["is_branch_query"]:
        if not is_title_branch:
            return -0.20, "branch_query_hq_title_mismatch"
        # Both query and title carry a branch token — accept if any token
        # overlaps (e.g. query "号百" matches title "号百分公司").
        query_branches = scope["query_branches"]
        has_match = any(
            qb in title_branches
            or any(qb in tb for tb in title_branches)
            or any(tb in qb for tb in title_branches)
            for qb in query_branches
        )
        if has_match:
            return 0.15, "branch_scope_match"
        return -0.10, "branch_scope_wrong_branch"

    if scope["is_hq_query"]:
        if is_title_branch:
            return -0.15, "hq_query_branch_title_mismatch"
        return 0.10, "hq_scope_match"

    # Query has no explicit scope signal — prefer HQ candidates slightly so a
    # generic query no longer surfaces a branch regulation at top-1 (KB-009).
    if is_title_branch:
        return -0.05, "no_scope_branch_title_penalty"
    return 0.0, "no_scope_signal"


# --- Regulation-phrase exact match (SPEC Phase 3.1 family discrimination) -----
# Two titles can both contain the query's regulation phrase as a substring but
# belong to different regulation families — e.g. "合规管理办法" (exact) vs
# "重要决策法律合规审核管理办法" (longer, more specific).  Without a phrase-
# exact signal, lexical title overlap boosts both equally and the longer
# (more specific) family can outrank the exact family at top-1.
#
# We split each query regulation phrase into prefix + suffix (e.g. "合规" +
# "管理办法") and check what appears between them in the *title*:
#   * query prefix appears, immediately followed by the suffix → exact match
#     (the title's regulation family literally equals the query's)
#   * query prefix appears, but extra CJK chars sit between it and the suffix
#     → title is a more specific regulation family; the exact family doc
#     (if present in the pool) must outrank it.
#   * query prefix not in title → no signal.

_REGULATION_QUERY_RE = re.compile(
    r"([\u4e00-\u9fff]{2,12}(?:管理办法|管理规定|实施细则|管理制度|实施办法|规定|细则|制度))"
)
_REGULATION_SUFFIXES = (
    "管理办法",
    "管理规定",
    "实施细则",
    "管理制度",
    "实施办法",
    "规定",
    "细则",
    "制度",
)


def compute_regulation_phrase_signal(query: str, title: str) -> tuple[float, str]:
    """Return ``(signal, reason)`` for regulation-family exact match.

    Returns the signal as a float in ``[-0.08, +0.10]`` and a short reason
    code suitable for audit logging.

    * query prefix + suffix appear verbatim adjacent in title → +0.10
    * query prefix appears but is followed by extra CJK chars before the
      suffix (title is a more specific regulation family) → -0.08
    * query has no regulation phrase, or prefix not in title → 0.00
    """
    q = query or ""
    title_l = title or ""
    q_phrases = _REGULATION_QUERY_RE.findall(q)
    if not q_phrases:
        return 0.0, "no_query_regulation_phrase"
    # Deduplicate while preserving order; longest first so a more specific
    # query phrase (e.g. "差旅费管理办法" over "办法") wins the boost.
    q_phrases = sorted(set(q_phrases), key=len, reverse=True)
    best_signal = 0.0
    best_reason = "regulation_phrase_no_match"
    for qp in q_phrases:
        for suf in _REGULATION_SUFFIXES:
            if not qp.endswith(suf) or len(qp) <= len(suf):
                continue
            prefix = qp[: -len(suf)]
            if len(prefix) < 2:
                break
            idx = title_l.find(prefix)
            if idx < 0:
                continue  # prefix not in title; try next suffix/phrase
            after_idx = idx + len(prefix)
            tail = title_l[after_idx : after_idx + len(suf)]
            if tail == suf:
                # Query prefix + suffix appear verbatim adjacent in title —
                # title's regulation family literally equals the query's.
                if best_signal < 0.10:
                    best_signal = 0.10
                    best_reason = "regulation_phrase_exact_match"
                break
            # Prefix appears, but extra chars sit before the suffix — title
            # belongs to a more specific regulation family.  Confirm the
            # suffix still appears within a small window so we don't fire on
            # unrelated prefix matches.
            window = title_l[after_idx : after_idx + 12]
            if suf in window:
                if best_signal > -0.08:
                    best_signal = -0.08
                    best_reason = "regulation_phrase_title_more_specific"
                break
    return best_signal, best_reason


# Number-unit hits whose number is a FILTER condition (year / doc number /
# version) rather than the ANSWER the user wants. Such numbers must not trigger
# the "numeric question without exact unit match" score cap (SPEC Phase 3.4).
# A year is any 4-digit 19xx/20xx value; 文号 is "<digits>号"; 版本 is "第N版/
# N版/修订". These act as constraints, not as the requested value.
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_DOC_NUMBER_RE = re.compile(r"\d+号")
_VERSION_NUMBER_RE = re.compile(r"第[一二三四五六七八九十0-9]+版|[0-9]+版|修订")
# Answer-value units: amounts, percentages, time limits, counts. When the query
# asks for one of these, the candidate MUST contain the matching number+unit to
# be considered strong evidence (anti-confusion for II类/III类 style clauses).
_ANSWER_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*"
    r"(?:%|％|元|万元|亿|倍|天|周|个月|月|年|次|户|人|个|米|公里|千米|kg|g|kw|w)",
    re.IGNORECASE,
)


def _answer_numeric_hits(query: str) -> list:
    """Return number-unit hits in the query that look like the REQUESTED answer
    value (amount / percent / limit / count), excluding pure filter numbers
    (years, doc numbers, version ordinals).

    Examples:
      - "2025年差旅费办法 区外交通费标准" → [] (2025 is a filter year)
      - "技能竞赛团体奖金限额 2026年修订" → [] (2026 is a filter year)
      - "II类账户年付款限额" → [] (II is a category, not a value query)
      - "60米" → [60米] (answer value with unit)
      - "实名制扣分阈值 99%" → [99%] (answer value)
    """
    q = query or ""
    all_hits = extract_number_units(q)
    if not all_hits:
        return []
    years = set(_YEAR_RE.findall(q))
    doc_num_tokens = set()
    for m in _DOC_NUMBER_RE.findall(q):
        doc_num_tokens.update(re.findall(r"\d+", m))
    answer_hits = []
    for h in all_hits:
        # Skip years (2025年 is a filter, not an answer amount).
        if h.number in years:
            continue
        # Skip bare doc-number tokens like "44号" (number part matches).
        if h.number in doc_num_tokens:
            continue
        answer_hits.append(h)
    # If after removing filters there are still number+unit phrases whose unit
    # is an answer-type unit, treat them as answer-value queries.
    return [h for h in answer_hits if _ANSWER_UNIT_RE.search(h.phrase)]


def score_candidate_relevance(query: str, item: dict) -> dict[str, Any]:
    """Compute multi-signal relevance features and a final score in [0, 1]."""
    text = _candidate_text(item)
    title = str(item.get("title") or "")
    terms = extract_query_terms(query)
    text_l = text.lower()
    title_l = title.lower()

    covered = 0
    for t in terms:
        if t.lower() in text_l or t.lower() in title_l:
            covered += 1
    query_term_coverage = (covered / len(terms)) if terms else 0.0

    # Phrase-ish: consecutive bigrams of CJK terms present
    cjk_terms = [t for t in _CJK_TERM_RE.findall(query or "") if t not in ("多少", "什么")]
    phrase_hits = 0
    phrase_total = max(0, len(cjk_terms) - 1)
    for i in range(phrase_total):
        phrase = cjk_terms[i] + cjk_terms[i + 1]
        if phrase in text or phrase in title:
            phrase_hits += 1
    phrase_coverage = (phrase_hits / phrase_total) if phrase_total else query_term_coverage

    title_hits = sum(1 for t in terms if t.lower() in title_l)
    title_score = (title_hits / len(terms)) if terms else 0.0

    # Character-level title overlap (CJK titles are continuous; term-window
    # matching misses related titles like "涉诈涉骚扰电话号码处置细则" for query
    # term "涉诈电话"). Use the fraction of distinct CJK query characters that
    # appear in the title as a complementary signal, blended but never allowed
    # alone to push above the lexical title_score by more than a small margin.
    q_cjk_chars = set(_CJK_TERM_RE.findall(query or ""))
    q_cjk_chars = {ch for run in q_cjk_chars for ch in run}
    if q_cjk_chars and title_l:
        title_char_hits = sum(1 for ch in q_cjk_chars if ch in title)
        title_char_overlap = title_char_hits / len(q_cjk_chars)
        # Blend: keep window-based title_score authoritative, but lift it when
        # nearly all query characters appear in the title (strong institutional
        # title). Cap the lift so a single shared character cannot dominate.
        title_score = max(title_score, min(title_char_overlap, title_score + 0.25))

    # Semantic feature — symmetric across tools (SPEC Phase 1.4).
    # ``item["score"]`` is a pipeline artifact that differs between search
    # (boosted retrieval score, often ~1.0) and ask (RRF/rerank number, often
    # <0.05). Reading it directly produces the KB-017 divergence (search=1.0,
    # ask=0.0957 for the same document). Instead we derive a verifiable
    # semantic proxy purely from how much of the query the candidate's
    # text+title actually cover. This is identical for both tools and cannot
    # be inflated by a tool-specific pipeline score, so the same evidence
    # always gets the same accept decision.
    semantic_score = max(
        query_term_coverage,
        0.6 * query_term_coverage + 0.4 * title_score,
    )
    # SPEC Phase 3.3: a candidate surfaced by an alias-expanded FTS variant
    # (e.g. 防诈骗→涉诈) is verifiable lexical evidence — the canonical term
    # matched the document even though the user's colloquial phrasing has low
    # term-window coverage. Credit it so the evidence clears the gate. This
    # flag is set ONLY by the shared _retrieve_candidates path for FTS hits on
    # expanded variants (never for the original query), so it cannot fire on
    # ordinary no-answer queries whose FTS matches are generic word overlap.
    if item.get("alias_fts_match"):
        semantic_score = max(semantic_score, 0.55)
    # Genuine vector-similarity signal from the shared semantic_search path
    # (tagged ``_semantic_similarity`` by _retrieve_candidates). A high vector
    # similarity (e.g. 1.0 for a synonym match 防诈骗≈涉诈) is strong evidence
    # that lexical coverage underestimates; floor the semantic feature by it.
    # This tag is set ONLY on search/probe candidates, never on ask source
    # dicts, so it cannot create a search/ask asymmetry.
    try:
        _vec_sim = float(item.get("_semantic_similarity") or 0.0)
    except (TypeError, ValueError):
        _vec_sim = 0.0
    if _vec_sim >= 0.8 and _vec_sim > semantic_score:
        semantic_score = _vec_sim
    # SPEC Phase 3.3: reranker score floor. The cross-encoder reranker scores
    # candidates on semantic relevance, which captures synonym matches and
    # paraphrase that lexical coverage misses (e.g. 比赛→劳动竞赛). A high
    # rerank score (>=0.7) is strong evidence that the candidate is the
    # correct answer even when lexical features are weak. The floor is
    # conservative: it only lifts the semantic feature, not the final score
    # directly, and the 0.7 threshold filters out low-confidence reranks.
    try:
        _rerank = float(item.get("rerank_score") or 0.0)
    except (TypeError, ValueError):
        _rerank = 0.0
    if _rerank >= 0.7 and _rerank > semantic_score:
        semantic_score = _rerank

    # Store rerank_score for the boost section below (Phase 3.3).
    _rerank_score = _rerank

    fts_score = float(item.get("fts_score") or item.get("fts_rank") or 0.0)
    if fts_score > 1.0:
        # raw FTS ranks are often large negative/positive; clamp via existing field if present
        fts_score = min(1.0, abs(fts_score) / 20.0)

    nu = score_numeric_unit_match(query, text)
    features = nu.get("features") or {}
    numeric_unit_score = 0.0
    if features.get("exact_number_unit_match"):
        numeric_unit_score = 1.0
    elif features.get("number_match_unit_mismatch"):
        numeric_unit_score = 0.0
    elif _answer_numeric_hits(query):
        # Query asks for a numeric answer value but the candidate lacks an
        # exact number+unit match — weak evidence.
        numeric_unit_score = 0.15
    else:
        numeric_unit_score = 0.5  # N/A — neutral (filter-only or no numbers)

    freshness_score = 0.5
    # Only true live-external queries (today/live quotes/news) get the freshness
    # penalty. Local-version queries ("最新版本") are answerable from the corpus
    # and must be scored like ordinary queries.
    if classify_query_intent(query) == "live_external":
        freshness_score = 0.1  # local corpus is not live market data

    # Weighted blend — keyword-only FTS cannot dominate weak partial hits,
    # but full query-term coverage is strong lexical evidence.
    final = (
        0.25 * min(1.0, max(0.0, semantic_score))
        + 0.15 * min(1.0, max(0.0, fts_score))
        + 0.15 * title_score
        + 0.25 * query_term_coverage
        + 0.10 * phrase_coverage
        + 0.05 * numeric_unit_score
        + 0.05 * freshness_score
    )
    # Strong lexical evidence: nearly all query terms appear in the candidate.
    _has_answer_numeric = bool(_answer_numeric_hits(query))
    # SPEC Phase 3.3: track which boosts/penalties fired so the canonical
    # snapshot can record a per-candidate ranking_reason for auditability.
    _boosts_fired: list[str] = []
    _penalties_fired: list[str] = []
    if query_term_coverage >= 0.8:
        final = max(final, 0.40 + 0.25 * query_term_coverage)
        _boosts_fired.append("high_term_coverage")
    if query_term_coverage >= 1.0 and not _has_answer_numeric:
        final = max(final, 0.55)
        _boosts_fired.append("full_coverage")
    if phrase_coverage >= 0.5 and query_term_coverage >= 0.5:
        final = max(final, 0.45)
        _boosts_fired.append("strong_phrase_coverage")

    # Strong title evidence for institutional / policy documents:
    # title hits multiple multi-char terms → enough to answer even with weak
    # raw semantic scores (common when embeddings are sparse). A bare filter
    # year (2025年) does NOT disqualify a candidate from this title boost.
    _is_live = classify_query_intent(query) == "live_external"
    if (
        not _is_live
        and not _has_answer_numeric
        and title_score >= 0.35
        and query_term_coverage >= 0.35
    ):
        final = max(final, 0.55)
        _boosts_fired.append("strong_title_evidence")
    if (
        not _is_live
        and not _has_answer_numeric
        and title_score >= 0.5
    ):
        final = max(final, 0.50)
        _boosts_fired.append("high_title_score")

    # SPEC Phase 3.2: alias-matched candidates (retrieved via synonym expansion
    # of colloquial terms like 比赛→竞赛, 店铺→门店) are verifiable lexical
    # evidence. When the candidate also shows at least minimal term overlap
    # (the alias synonym words are now in the term set, so a candidate that
    # contains 竞赛/奖励 gets query_term_coverage ≈ 0.1–0.2), credit it so the
    # evidence clears the gate. The ``alias_fts_match`` flag is set ONLY by the
    # shared _retrieve_candidates path for hits on alias-expanded variants, so
    # this boost cannot fire on ordinary no-answer candidates whose FTS matches
    # are incidental word overlap.
    #
    # SPEC Phase 3.3: when multiple alias-matched candidates compete, preserve
    # the semantic similarity as a tiebreaker so a higher-similarity match
    # outranks a lower one even after the freshness/year boost is applied.
    # Without this, two different regulations that both contain the synonym
    # (e.g. 劳动竞赛 vs 技能竞赛) tie at 0.40 and the newer year wins, burying
    # the correct document.
    if item.get("alias_fts_match") and query_term_coverage >= 0.1:
        final = max(final, 0.40)
        _boosts_fired.append("alias_fts_match")
        if semantic_score > 0.5:
            final = max(final, 0.40 + (semantic_score - 0.5) * 0.3)
            _boosts_fired.append("alias_semantic_tiebreaker")

    # SPEC Phase 3.3: high-confidence reranker boost. When the cross-encoder
    # reranker assigns a score >= 0.7, it has independently verified semantic
    # relevance (synonym match, paraphrase, conceptual overlap) that lexical
    # coverage cannot capture. Floor the final score at 0.40 so the candidate
    # clears the 0.35 threshold. The 0.7 threshold is conservative: the
    # reranker's default min_score is 0.3, so only candidates the reranker is
    # genuinely confident about receive this boost. Without it, colloquial
    # queries (比赛/奖金) that match formal documents (劳动竞赛) via the
    # reranker are rejected at the relevance gate despite being the correct
    # answer.
    if _rerank_score >= 0.7:
        final = max(final, 0.40)
        _boosts_fired.append("reranker_high_confidence")

    # SPEC Phase 3.3: core-term title boost for colloquial queries.
    # Long colloquial queries produce many n-gram terms (50+) that dilute
    # query_term_coverage. But when the candidate TITLE contains 3+ distinct
    # 2-char CJK terms from the query AND the title carries a regulation
    # suffix (办法/规定/制度/通知/规范), the candidate is very likely the
    # correct regulation document — the core subject terms (线上/合作/公司)
    # match even though the colloquial modifiers (卖东西/店铺) do not.
    # This boost is conservative: it requires BOTH multi-term title overlap
    # AND a regulation-family suffix, so it cannot fire on unrelated FTS hits
    # that happen to share 2-3 generic words.
    _core_term_title_boosted = False
    if (
        not _is_live
        and not _has_answer_numeric
        and title_score >= 0.05
    ):
        title_2char_hits = sum(
            1 for t in terms
            if len(t) == 2 and t.lower() in title_l
        )
        if title_2char_hits >= 3 and re.search(
            r"(办法|规定|制度|通知|规范|管理条例|操作规程)",
            title,
        ):
            final = max(final, 0.42)
            _core_term_title_boosted = True
            _boosts_fired.append("core_term_title_boost")

    # Hard penalties
    if features.get("number_match_unit_mismatch"):
        final *= 0.35
        _penalties_fired.append("numeric_unit_mismatch")
    # Numeric-question penalty — BUT only for numbers the user wants as an
    # ANSWER (金额/比例/时限/限额). Numbers that act as a FILTER condition
    # (年份、文号、版本号、修订年份) must NOT cap the score, otherwise a query
    # like "2025年差旅费办法" is wrongly pushed below threshold just because the
    # candidate text does not repeat "2025年" verbatim (SPEC Phase 3.4).
    answer_numeric_hits = _answer_numeric_hits(query)
    if answer_numeric_hits and not features.get("exact_number_unit_match"):
        final = min(final, 0.34)
        _penalties_fired.append("answer_numeric_no_exact_match")
    if _is_live:
        final = min(final, 0.25)
        _penalties_fired.append("live_external_cap")
    # Single generic term overlap (e.g. only "营收") is not enough for a
    # specific numeric/entity question with many unused terms.
    # SPEC Phase 3.3: do NOT apply this cap when the core-term title boost
    # fired — the boost already verified that the title contains 3+ distinct
    # query terms AND a regulation suffix, which is strong evidence that the
    # candidate is the correct regulation document despite low overall coverage
    # (the coverage is diluted by n-gram splitting of colloquial modifiers).
    if (
        terms
        and query_term_coverage < 0.4
        and semantic_score < 0.5
        and title_score < 0.35
        and not _core_term_title_boosted
    ):
        final = min(final, 0.30)
        _penalties_fired.append("low_coverage_cap")

    # Organizational scope alignment (SPEC Phase 3.1): apply a small additive
    # boost/penalty so HQ-vs-branch confusion no longer drives wrong-family
    # top-1 picks.  The signal is parsed from the candidate title (which encodes
    # the issuing org) and the query's explicit scope.
    scope_signal, scope_reason = compute_scope_signal(query, title)
    final += scope_signal
    if scope_signal > 0:
        _boosts_fired.append(f"scope:{scope_reason}")
    elif scope_signal < 0:
        _penalties_fired.append(f"scope:{scope_reason}")

    # Regulation-family exact match (SPEC Phase 3.1): when the query names a
    # specific regulation (e.g. "合规管理办法") and the candidate title
    # contains that prefix immediately followed by the regulation suffix, the
    # candidate is the exact family — boost it.  When the prefix is followed
    # by extra CJK chars before the suffix (e.g. "合规" → "审核管理办法"),
    # the candidate is a more specific regulation family — penalize so the
    # exact family doc outranks it when both are in the candidate pool.
    reg_signal, reg_reason = compute_regulation_phrase_signal(query, title)
    final += reg_signal
    if reg_signal > 0:
        _boosts_fired.append(f"regulation:{reg_reason}")
    elif reg_signal < 0:
        _penalties_fired.append(f"regulation:{reg_reason}")

    final = max(0.0, min(1.0, final))

    # SPEC Phase 3.3: build a structured ranking_reason for audit logging.
    # ``primary_signal`` is the strongest driver; when no boost/penalty fired,
    # the base weighted blend is the only signal.
    if _boosts_fired:
        primary_signal = _boosts_fired[0]
    elif _penalties_fired:
        primary_signal = _penalties_fired[0]
    else:
        primary_signal = "base_blend"
    ranking_reason = {
        "primary_signal": primary_signal,
        "boosts": list(_boosts_fired),
        "penalties": list(_penalties_fired),
        "scope_reason": scope_reason,
        "regulation_phrase_reason": reg_reason,
        "intent": classify_query_intent(query),
        "alias_fts_match": bool(item.get("alias_fts_match")),
        "rerank_score": round(_rerank_score, 4),
        "core_term_title_boosted": _core_term_title_boosted,
    }
    return {
        "semantic_score": round(semantic_score, 4),
        "fts_score": round(fts_score, 4),
        "title_score": round(title_score, 4),
        "numeric_unit_score": round(numeric_unit_score, 4),
        "phrase_coverage": round(phrase_coverage, 4),
        "query_term_coverage": round(query_term_coverage, 4),
        "freshness_score": round(freshness_score, 4),
        "scope_signal": round(scope_signal, 4),
        "scope_reason": scope_reason,
        "regulation_phrase_signal": round(reg_signal, 4),
        "regulation_phrase_reason": reg_reason,
        "final_relevance_score": round(final, 4),
        "features": features,
        "ranking_reason": ranking_reason,
    }


def apply_relevance_scores(query: str, items: list[dict]) -> list[dict]:
    for item in items:
        scores = score_candidate_relevance(query, item)
        item["relevance"] = scores
        item["final_relevance_score"] = scores["final_relevance_score"]
        # Expose blended score for downstream thresholds without clobbering
        # a higher semantic score when already present.
        if "score" not in item or item.get("score") is None:
            item["score"] = scores["final_relevance_score"]
    items.sort(
        key=lambda x: float(x.get("final_relevance_score") or x.get("score") or 0.0),
        reverse=True,
    )
    return items


def evaluate_evidence(
    query: str,
    items: list[dict],
    *,
    threshold: float = 0.35,
) -> dict[str, Any]:
    """Return gate decision for a candidate list."""
    if is_current_information_query(query):
        # Local KB cannot answer live / "today" questions unless evidence is
        # extremely strong and explicitly fresh — default to no-answer.
        return {
            "accept": False,
            "no_match": True,
            "reason": "requires_current_external_data",
            "top_score": 0.0,
            "threshold": threshold,
            "items": [],
        }

    if classify_query_intent(query) == "out_of_domain":
        # Mirror the unified gate: out-of-domain queries short-circuit to
        # no-answer so the older ``evaluate_evidence`` callers (still used by
        # a few tests and helpers) do not produce a contradicting decision.
        ood_reason = out_of_domain_reason(query) or "out_of_domain"
        return {
            "accept": False,
            "no_match": True,
            "reason": f"out_of_domain:{ood_reason}",
            "top_score": 0.0,
            "threshold": threshold,
            "items": [],
        }

    if not items:
        return {
            "accept": False,
            "no_match": True,
            "reason": "no_candidates",
            "top_score": 0.0,
            "threshold": threshold,
            "items": [],
        }

    ranked = apply_relevance_scores(query, list(items))
    top = float(ranked[0].get("final_relevance_score") or 0.0)
    accepted = [r for r in ranked if float(r.get("final_relevance_score") or 0.0) >= threshold]

    if not accepted or top < threshold:
        return {
            "accept": False,
            "no_match": True,
            "reason": "insufficient_relevant_evidence",
            "top_score": round(top, 4),
            "threshold": threshold,
            "items": [],
        }

    return {
        "accept": True,
        "no_match": False,
        "reason": None,
        "top_score": round(top, 4),
        "threshold": threshold,
        "items": accepted,
    }


# --- Unified evidence judgment (SPEC Phase 1) --------------------------------
# ``search`` and ``ask`` previously evaluated the same evidence with different
# input objects (search re-scored enriched candidates; ask fed pipeline source
# dicts whose ``score`` was a tiny RRF/rerank number). This produced the
# KB-017 divergence (search score=1.0, ask top_score=0.0957).
#
# The unified entry below normalizes any candidate shape (search result OR ask
# source dict) into the same field set before scoring, so both tools see the
# same ``final_relevance_score`` for the same evidence.

_NORMALIZE_TEXT_KEYS = (
    "text", "content", "chunk_text", "summary",
)


def normalize_evidence_candidate(item: dict) -> dict:
    """Return a canonical candidate dict for :func:`score_candidate_relevance`.

    Merges the many field names used across search results and ask source dicts
    (``knowledge_id``/``page_id``, ``title``/``document``, ``text``/``content``,
    ``block_id``/``id``) so the relevance scorer reads identical inputs from
    either tool. Preserves the original object and only copies fields.
    """
    if not isinstance(item, dict):
        return {}
    out = dict(item)
    # knowledge id
    kid = item.get("knowledge_id") or item.get("page_id") or ""
    if kid and not out.get("knowledge_id"):
        out["knowledge_id"] = kid
    # block id
    bid = item.get("block_id") or ""
    if not bid and item.get("id") and item.get("id") != kid:
        bid = item.get("id")
    if bid and not out.get("block_id"):
        out["block_id"] = bid
    # title — citation dicts carry ``document`` instead of ``title``
    title = item.get("title")
    if not title:
        cit = item.get("citation")
        if isinstance(cit, dict):
            title = cit.get("document") or cit.get("title")
    if not title:
        title = item.get("document")
    if title and not out.get("title"):
        out["title"] = title
    # text — prefer the most informative non-empty value
    text = ""
    for k in _NORMALIZE_TEXT_KEYS:
        v = item.get(k)
        if v and len(str(v)) > len(str(text)):
            text = v
    if text and not out.get("text"):
        out["text"] = text
    # Score: keep an explicit pipeline score on ``score`` for the semantic
    # feature, but DO NOT let a tiny RRF/rerank number dominate the blended
    # final score. score_candidate_relevance reads ``score`` as the semantic
    # feature (weight 0.25); for ask source dicts that number is often <0.05,
    # which is correct — the lexical/title/phrase features still drive the
    # accept decision. For search candidates that already carry a high score
    # (e.g. 1.0) we keep it so behavior is unchanged.
    return out


def evaluate_evidence_unified(
    query: str,
    items: list[dict],
    *,
    threshold: float = 0.35,
) -> dict[str, Any]:
    """Single source of truth for answerable-evidence judgment.

    Both ``search`` and ``ask`` must call this (ask before generation) so the
    accept/reject decision is identical for the same candidate set. The output
    is the same shape as :func:`evaluate_evidence`, plus a per-candidate
    ``evidence`` list with knowledge_id / block_id / relevance for traceability.
    """
    intent = classify_query_intent(query)
    if intent == "live_external":
        return {
            "accept": False,
            "no_match": True,
            "reason": "requires_current_external_data",
            "top_score": 0.0,
            "threshold": threshold,
            "items": [],
            "intent": intent,
        }
    if intent == "out_of_domain":
        # SPEC Phase 3.2 §3.3: queries clearly outside the corpus domain
        # must short-circuit to no-answer with a distinct, auditable reason
        # code (not the generic ``insufficient_relevant_evidence``). This
        # prevents false-positive answers built on weak lexical overlap.
        ood_reason = out_of_domain_reason(query) or "out_of_domain"
        return {
            "accept": False,
            "no_match": True,
            "reason": f"out_of_domain:{ood_reason}",
            "top_score": 0.0,
            "threshold": threshold,
            "items": [],
            "intent": intent,
        }

    if not items:
        return {
            "accept": False,
            "no_match": True,
            "reason": "no_candidates",
            "top_score": 0.0,
            "threshold": threshold,
            "items": [],
            "intent": intent,
        }

    # Normalize every candidate to the canonical shape, then score uniformly.
    normalized = [normalize_evidence_candidate(i) for i in items if isinstance(i, dict)]
    normalized = [n for n in normalized if n]
    ranked = apply_relevance_scores(query, normalized)
    top = float(ranked[0].get("final_relevance_score") or 0.0)
    accepted = [r for r in ranked if float(r.get("final_relevance_score") or 0.0) >= threshold]

    if not accepted or top < threshold:
        return {
            "accept": False,
            "no_match": True,
            "reason": "insufficient_relevant_evidence",
            "top_score": round(top, 4),
            "threshold": threshold,
            "items": [],
            "intent": intent,
        }

    evidence_summary = [
        {
            "knowledge_id": r.get("knowledge_id") or "",
            "block_id": r.get("block_id") or "",
            "title": (r.get("title") or "")[:120],
            "final_relevance_score": r.get("final_relevance_score"),
            "relevance": r.get("relevance"),
        }
        for r in accepted
    ]
    return {
        "accept": True,
        "no_match": False,
        "reason": None,
        "top_score": round(top, 4),
        "threshold": threshold,
        "items": accepted,
        "evidence": evidence_summary,
        "intent": intent,
    }

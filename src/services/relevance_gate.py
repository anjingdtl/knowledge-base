"""Unified relevance / no-answer gate for semantic, FTS, and hybrid hits.

FTS keyword hits alone are not sufficient evidence to answer a question.
"""
from __future__ import annotations

import re
from typing import Any

from src.services.numeric_unit_match import extract_number_units, score_numeric_unit_match

# --- Intent classification (SPEC Phase 2) -----------------------------------
# A binary "最新 = live external" rule mis-routes local version queries to the
# no-answer short-circuit (KB-035/KB-037). We now split three intents:
#   * live_external   — needs real-time external data (today/live quotes/news)
#   * local_version   — compares versions inside the local corpus (最新版本/最新修订版/现行办法)
#   * ordinary        — everything else (normal retrieval)
# Only ``live_external`` may short-circuit to ``requires_current_external_data``.

# Strong live-data signals. These words almost always require an external,
# real-time source that a local policy corpus cannot provide.
_LIVE_EXTERNAL_STRONG_RE = re.compile(
    r"(今天|今日|实时|行情|股价|此刻|刚刚|当前市|实时行情|最新进展|最新动态|最新消息)",
)

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


def classify_query_intent(query: str) -> str:
    """Return one of: ``live_external`` / ``local_version`` / ``ordinary``.

    Order matters: a query that explicitly asks for a local document version
    (e.g. "差旅费管理办法最新版本是哪一年") must be treated as ``local_version``
    even if it contains a live-looking word. A query with a strong live signal
    and no version noun is ``live_external``.
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

    return "ordinary"


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
    if query_term_coverage >= 0.8:
        final = max(final, 0.40 + 0.25 * query_term_coverage)
    if query_term_coverage >= 1.0 and not _has_answer_numeric:
        final = max(final, 0.55)
    if phrase_coverage >= 0.5 and query_term_coverage >= 0.5:
        final = max(final, 0.45)

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
    if (
        not _is_live
        and not _has_answer_numeric
        and title_score >= 0.5
    ):
        final = max(final, 0.50)

    # Hard penalties
    if features.get("number_match_unit_mismatch"):
        final *= 0.35
    # Numeric-question penalty — BUT only for numbers the user wants as an
    # ANSWER (金额/比例/时限/限额). Numbers that act as a FILTER condition
    # (年份、文号、版本号、修订年份) must NOT cap the score, otherwise a query
    # like "2025年差旅费办法" is wrongly pushed below threshold just because the
    # candidate text does not repeat "2025年" verbatim (SPEC Phase 3.4).
    answer_numeric_hits = _answer_numeric_hits(query)
    if answer_numeric_hits and not features.get("exact_number_unit_match"):
        final = min(final, 0.34)
    if _is_live:
        final = min(final, 0.25)
    # Single generic term overlap (e.g. only "营收") is not enough for a
    # specific numeric/entity question with many unused terms.
    if terms and query_term_coverage < 0.4 and semantic_score < 0.5 and title_score < 0.35:
        final = min(final, 0.30)

    final = max(0.0, min(1.0, final))
    return {
        "semantic_score": round(semantic_score, 4),
        "fts_score": round(fts_score, 4),
        "title_score": round(title_score, 4),
        "numeric_unit_score": round(numeric_unit_score, 4),
        "phrase_coverage": round(phrase_coverage, 4),
        "query_term_coverage": round(query_term_coverage, 4),
        "freshness_score": round(freshness_score, 4),
        "final_relevance_score": round(final, 4),
        "features": features,
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

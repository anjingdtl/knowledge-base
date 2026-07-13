"""Verified hybrid answer assembly: conflict disclosure, citations, answer_mode.

Phase 4 Spec §7.7–§8 / §13.2. SearchService remains the sole fusion orchestrator;
this module packages ask payloads from search results (+ optional LLM answer).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from src.services.verified_conflict import (
    detect_claim_conflicts,
    filter_stale_claims,
    is_freshness_sensitive_query,
)

logger = logging.getLogger(__name__)

ANSWER_MODE_HYBRID = "hybrid_verified"
ANSWER_MODE_RAW = "raw_only"
ANSWER_MODE_CONFLICT = "conflict_disclosure"
ANSWER_MODE_NO_ANSWER = "no_answer"


def _is_claim(row: dict[str, Any]) -> bool:
    return bool(
        row.get("source") == "verified_claim"
        or row.get("candidate_type") == "claim"
        or bool(row.get("claim_id"))
    )


def _is_raw(row: dict[str, Any]) -> bool:
    return bool(
        not _is_claim(row)
        and (
            row.get("source") in (None, "knowledge", "wiki")
            or row.get("candidate_type") == "raw_block"
            or row.get("block_id")
            or row.get("knowledge_id")
        )
    )


def build_claim_citations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claim + evidence chain citations (Spec §8.1–§8.2)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if not _is_claim(row):
            continue
        evidence = []
        for ev in row.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            evidence.append({
                "knowledge_id": ev.get("knowledge_id") or "",
                "block_id": ev.get("block_id") or "",
                "path": ev.get("path") or "",
                "location": ev.get("location") or {},
                "excerpt": ev.get("excerpt") or "",
                "evidence_stance": ev.get("stance") or ev.get("evidence_stance") or "supports",
                "stale": bool(ev.get("stale")),
            })
        cit = {
            "claim_id": row.get("claim_id") or row.get("candidate_id"),
            "statement": row.get("text") or row.get("statement") or "",
            "status": row.get("status") or "active",
            "revision": row.get("revision"),
            "page_id": row.get("page_id"),
            "validation": "passed" if row.get("eligible", True) else "disclose",
            "evidence": evidence,
        }
        # Never present claim without at least one evidence slot when available
        if not evidence and row.get("block_id"):
            cit["evidence"] = [{
                "knowledge_id": row.get("knowledge_id") or "",
                "block_id": row.get("block_id") or "",
                "path": "",
                "location": {},
                "excerpt": (row.get("text") or "")[:200],
                "evidence_stance": "supports",
                "stale": False,
            }]
        out.append(cit)
    return out


def build_raw_evidence_used(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if _is_claim(row):
            # Evidence already on claim citation
            continue
        out.append({
            "knowledge_id": row.get("knowledge_id") or "",
            "block_id": row.get("block_id") or "",
            "title": row.get("title") or "",
            "path": (row.get("citation") or {}).get("path", "") if isinstance(row.get("citation"), dict) else "",
            "text": (row.get("text") or "")[:500],
            "score": row.get("score"),
            "citation": row.get("citation"),
        })
    return out


def _format_conflict_answer(conflicts: list[dict[str, Any]], question: str) -> str:
    lines = [
        f"针对问题「{question}」，知识库中存在相互冲突的已验证结论，"
        "系统不会自动选择某一方作为唯一答案。主要分歧如下：",
        "",
    ]
    for i, c in enumerate(conflicts, 1):
        reasons = "、".join(c.get("reason_codes") or []) or "内容分歧"
        lines.append(f"### 分歧 {i}（{reasons}）")
        lines.append(f"- 观点 A（claim `{c.get('claim_a_id')}`）: {c.get('statement_a')}")
        lines.append(f"- 观点 B（claim `{c.get('claim_b_id')}`）: {c.get('statement_b')}")
        ea = c.get("evidence_a") or []
        eb = c.get("evidence_b") or []
        if ea:
            e0 = ea[0]
            lines.append(
                f"  - A 证据: knowledge={e0.get('knowledge_id')} block={e0.get('block_id')}"
            )
        if eb:
            e0 = eb[0]
            lines.append(
                f"  - B 证据: knowledge={e0.get('knowledge_id')} block={e0.get('block_id')}"
            )
        lines.append("")
    lines.append(
        "建议：对照双方原始 Evidence，结合来源更新时间与适用范围自行裁决；"
        "也可在 Authoring 模式提交冲突审阅。"
    )
    return "\n".join(lines)


def _format_no_answer(question: str, *, freshness: bool = False) -> str:
    if freshness:
        return (
            f"未能确认问题「{question}」的最新可靠结论。"
            "相关 Claim 可能已过期，或缺少可追溯的最新原始证据。"
            "请补充/更新来源文档后重试，或改用更具体的文档定位查询。"
        )
    return (
        f"知识库中未找到可回答「{question}」的充分证据。"
        "未返回仅有 Wiki 页面而无原始 Evidence 的结论。"
    )


def assemble_answer_payload(
    question: str,
    search_results: list[dict[str, Any]],
    *,
    llm_answer: str | None = None,
    search_trace: dict[str, Any] | None = None,
    disclose_claims: list[dict[str, Any]] | None = None,
    generate_fn: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """Build Spec §13.2 ask result fields from search results.

    When conflicts exist → conflict_disclosure without single-side pick.
    When no usable evidence → no_answer.
    Otherwise hybrid_verified or raw_only; optional LLM answer preferred if provided.
    """
    trace = dict(search_trace or {})
    warnings: list[str] = []
    fallbacks: list[dict[str, Any]] = list(trace.get("fallbacks") or [])

    freshness_q = is_freshness_sensitive_query(question)
    results = list(search_results or [])
    side_claims = list(disclose_claims or [])

    claim_rows = [r for r in results if _is_claim(r)]
    raw_rows = [r for r in results if _is_raw(r) and not _is_claim(r)]

    # Freshness: drop stale claims from primary conclusions
    claim_rows, dropped_stale = filter_stale_claims(claim_rows, drop_stale=True)
    if dropped_stale:
        warnings.append(f"excluded_stale_claims:{len(dropped_stale)}")
        for d in dropped_stale:
            warnings.append(f"stale_claim:{d.get('claim_id')}")

    # Conflict scan: primary claims + disclose_only side channel
    conflict_pool = claim_rows + [
        c for c in side_claims
        if c.get("disclose_only") or c.get("candidate_type") == "claim"
    ]
    conflicts = detect_claim_conflicts(conflict_pool)

    claims_used = build_claim_citations(claim_rows)
    raw_evidence_used = build_raw_evidence_used(raw_rows)

    # Promote claim evidence into raw_evidence_used for full chain visibility
    for cit in claims_used:
        for ev in cit.get("evidence") or []:
            if not (ev.get("knowledge_id") or ev.get("block_id")):
                continue
            raw_evidence_used.append({
                "knowledge_id": ev.get("knowledge_id") or "",
                "block_id": ev.get("block_id") or "",
                "title": "",
                "path": ev.get("path") or "",
                "text": ev.get("excerpt") or "",
                "score": None,
                "via_claim": cit.get("claim_id"),
                "evidence_stance": ev.get("evidence_stance"),
            })

    # Dedup raw evidence by (kid, bid)
    seen_ev: set[tuple[str, str]] = set()
    deduped_raw: list[dict[str, Any]] = []
    for e in raw_evidence_used:
        key = (str(e.get("knowledge_id") or ""), str(e.get("block_id") or ""))
        if key in seen_ev and key != ("", ""):
            continue
        seen_ev.add(key)
        deduped_raw.append(e)
    raw_evidence_used = deduped_raw

    # Trace fallbacks
    stage_fb = (trace.get("stages") or {}).get("fallback")
    if stage_fb:
        fallbacks.append({
            "from": "verified_wiki",
            "to": "raw_retrieval",
            "reason": str(stage_fb),
        })
    wiki_err = (trace.get("stages") or {}).get("verified_wiki", {}).get("error")
    if wiki_err:
        fallbacks.append({
            "from": "verified_wiki",
            "to": "raw_retrieval",
            "reason": str(wiki_err),
        })
        warnings.append(f"wiki_degraded:{wiki_err}")

    # --- Decide answer_mode ---
    if conflicts:
        answer_mode = ANSWER_MODE_CONFLICT
        answer = _format_conflict_answer(conflicts, question)
        # Attach both sides as claims_used for disclosure
        extra = build_claim_citations(side_claims)
        for c in extra:
            if c not in claims_used and c.get("claim_id") not in {
                x.get("claim_id") for x in claims_used
            }:
                claims_used.append(c)
    elif not claim_rows and not raw_rows:
        answer_mode = ANSWER_MODE_NO_ANSWER
        answer = _format_no_answer(question, freshness=freshness_q)
        warnings.append("no_answer")
    elif claim_rows:
        answer_mode = ANSWER_MODE_HYBRID
        if llm_answer and llm_answer.strip():
            answer = llm_answer.strip()
        elif generate_fn is not None:
            context = _build_generation_context(claim_rows, raw_rows, conflicts=[])
            try:
                answer = (generate_fn(question, context) or "").strip()
            except Exception as e:  # noqa: BLE001
                logger.warning("verified answer LLM failed: %s", e)
                answer = _fallback_hybrid_text(question, claim_rows, raw_rows)
                warnings.append(f"generate_failed:{e}")
        else:
            answer = _fallback_hybrid_text(question, claim_rows, raw_rows)
        # Spec: main conclusion must not cite wiki page only — ensure claims have evidence
        bare = [c for c in claims_used if not c.get("evidence")]
        if bare:
            warnings.append(f"claims_missing_evidence:{len(bare)}")
            # Strip bare claims from used set so product never claims wiki-only truth
            claims_used = [c for c in claims_used if c.get("evidence")]
            if not claims_used and raw_rows:
                answer_mode = ANSWER_MODE_RAW
    else:
        answer_mode = ANSWER_MODE_RAW
        if llm_answer and llm_answer.strip():
            answer = llm_answer.strip()
        elif generate_fn is not None:
            context = _build_generation_context([], raw_rows, conflicts=[])
            try:
                answer = (generate_fn(question, context) or "").strip()
            except Exception as e:  # noqa: BLE001
                logger.warning("raw-only answer LLM failed: %s", e)
                answer = _fallback_raw_text(question, raw_rows)
                warnings.append(f"generate_failed:{e}")
        else:
            answer = _fallback_raw_text(question, raw_rows)

    if freshness_q and answer_mode == ANSWER_MODE_HYBRID and dropped_stale:
        warnings.append("freshness_sensitive_stale_excluded")

    # Sources for backward-compatible ask payload
    sources = _build_sources(results, claim_rows, raw_rows)

    return {
        "answer": answer,
        "answer_mode": answer_mode,
        "conflict_disclosed": answer_mode == ANSWER_MODE_CONFLICT,
        "claims_used": claims_used,
        "raw_evidence_used": raw_evidence_used,
        "conflicts": conflicts,
        "fallbacks": fallbacks,
        "warnings": warnings,
        "sources": sources,
        "freshness_sensitive": freshness_q,
        "trace_id": trace.get("trace_id") or "",
        "search_trace": {
            "mode": trace.get("mode"),
            "route": trace.get("route"),
            "stages": trace.get("stages"),
            "sources": trace.get("sources"),
        },
    }


def _build_generation_context(
    claim_rows: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
    *,
    conflicts: list[dict[str, Any]],
) -> str:
    parts: list[str] = []
    if conflicts:
        parts.append(
            "【系统指令】以下 Claim 存在冲突，不得输出单一确定结论，须并列披露。"
        )
    for i, c in enumerate(claim_rows, 1):
        ev_bits = []
        for ev in (c.get("evidence") or [])[:3]:
            ev_bits.append(
                f"kid={ev.get('knowledge_id')} bid={ev.get('block_id')} "
                f"stance={ev.get('stance', 'supports')}"
            )
        parts.append(
            f"【已验证 Claim {i} id={c.get('claim_id')}】\n"
            f"{c.get('text')}\n证据: {'; '.join(ev_bits) or '（无）'}"
        )
    for i, r in enumerate(raw_rows[:8], 1):
        parts.append(
            f"【原始证据 {i} kid={r.get('knowledge_id')} bid={r.get('block_id')}】\n"
            f"{(r.get('text') or '')[:800]}"
        )
    return "\n\n".join(parts) if parts else "（无检索上下文）"


def _fallback_hybrid_text(
    question: str,
    claim_rows: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
) -> str:
    lines = [f"基于已验证知识与原始证据，关于「{question}」：", ""]
    for c in claim_rows[:5]:
        cid = c.get("claim_id") or ""
        lines.append(f"- {c.get('text')} 〔claim:{cid}〕")
        for ev in (c.get("evidence") or [])[:1]:
            lines.append(
                f"  证据 block={ev.get('block_id')} knowledge={ev.get('knowledge_id')}"
            )
    if raw_rows and len(claim_rows) < 3:
        lines.append("")
        lines.append("补充原始片段：")
        for r in raw_rows[:3]:
            lines.append(f"- [{r.get('title') or r.get('knowledge_id')}] {(r.get('text') or '')[:200]}")
    return "\n".join(lines)


def _fallback_raw_text(question: str, raw_rows: list[dict[str, Any]]) -> str:
    lines = [f"基于原始文档检索（未使用可验证 Claim 结论），关于「{question}」：", ""]
    for r in raw_rows[:5]:
        lines.append(
            f"- [{r.get('title') or r.get('knowledge_id')}] {(r.get('text') or '')[:300]}"
        )
    return "\n".join(lines)


def _build_sources(
    all_results: list[dict[str, Any]],
    claim_rows: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Backward-compatible sources list for MCP ask."""
    sources: list[dict[str, Any]] = []
    for c in claim_rows:
        primary_ev = (c.get("evidence") or [{}])[0]
        sources.append({
            "source": "verified_claim",
            "claim_id": c.get("claim_id"),
            "knowledge_id": c.get("knowledge_id") or primary_ev.get("knowledge_id") or "",
            "block_id": c.get("block_id") or primary_ev.get("block_id") or "",
            "title": c.get("title") or f"Claim: {(c.get('text') or '')[:60]}",
            "text": c.get("text") or "",
            "score": c.get("score"),
            "evidence": c.get("evidence") or [],
            "citation": c.get("citation"),
            "candidate_type": "claim",
            "source_layer": "canonical",
        })
    for r in raw_rows:
        sources.append({
            "source": r.get("source") or "knowledge",
            "knowledge_id": r.get("knowledge_id") or "",
            "block_id": r.get("block_id") or "",
            "title": r.get("title") or "",
            "text": r.get("text") or "",
            "score": r.get("score"),
            "citation": r.get("citation"),
            "candidate_type": "raw_block",
            "source_layer": "evidence",
        })
    return sources


class VerifiedAnswerService:
    """Orchestrate search → conflict/freshness → answer payload for ask."""

    def __init__(self, search_service: Any, llm: Any = None, config: Any = None):
        self._search = search_service
        self._llm = llm
        self._config = config or {}

    def _cfg(self, key: str, default=None):
        if isinstance(self._config, dict):
            parts = key.split(".")
            obj: object = self._config
            for p in parts:
                if isinstance(obj, dict):
                    obj = obj.get(p)
                else:
                    return default
            return obj if obj is not None else default
        return self._config.get(key, default)

    def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        use_llm: bool = True,
        llm_answer: str | None = None,
    ) -> dict[str, Any]:
        results = self._search.search(question, top_k=top_k)
        trace = dict(getattr(self._search, "last_search_trace", {}) or {})

        # Side-channel disclose claims collected during search
        disclose_ids = trace.get("disclose_claims") or []
        disclose_rows: list[dict[str, Any]] = []
        if disclose_ids and hasattr(self._search, "get_disclose_claim_rows"):
            try:
                disclose_rows = self._search.get_disclose_claim_rows()
            except Exception:  # noqa: BLE001
                disclose_rows = []
        elif hasattr(self._search, "last_disclose_claims"):
            disclose_rows = list(getattr(self._search, "last_disclose_claims") or [])

        generate_fn = None
        if use_llm and llm_answer is None and self._llm is not None:
            def generate_fn(q: str, context: str) -> str:
                from src.services.rag_pipeline import build_rag_messages
                from src.utils.llm_text import strip_think

                messages = build_rag_messages(q, context, [])
                if hasattr(self._llm, "chat_with_usage"):
                    content, _usage = self._llm.chat_with_usage(messages)
                    return strip_think(content)
                return strip_think(self._llm.chat(messages))

        payload = assemble_answer_payload(
            question,
            results,
            llm_answer=llm_answer,
            search_trace=trace,
            disclose_claims=disclose_rows,
            generate_fn=generate_fn,
        )
        # Standard ask fields
        payload.setdefault("source_graph", {
            "nodes": [], "edges": [], "truncated": False, "node_count": 0,
        })
        payload.setdefault("route", {
            "mode": payload["answer_mode"],
            "explanation": f"verified answer path: {payload['answer_mode']}",
            "search_mode": trace.get("mode"),
            "intent": (trace.get("route") or {}).get("intent"),
        })
        payload.setdefault("query_plan", {})
        payload.setdefault("block_contexts", {})
        payload.setdefault("wiki_context", "")
        return payload

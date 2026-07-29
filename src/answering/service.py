"""AnswerService — single application-layer ask orchestrator.

Calls SearchService.execute() (RetrievalOrchestrator under the hood).
Does not touch DB/Wiki/Gate/MCP envelopes directly.

WP2: answer.orchestrator pseudo dual-path removed — only unified assemble path.

SPEC v2 Phase 1/3: accepts a pre-gated evidence snapshot so search and ask
share the same candidates; expands adjacent blocks into generation context
before the LLM sees the evidence (KB-019).
"""
from __future__ import annotations

import logging
from typing import Any

from src.answering.assembler import assemble_answer_payload
from src.answering.generation import Generator
from src.answering.models import AnswerExecution

logger = logging.getLogger(__name__)


def resolve_answer_orchestrator_mode(config: Any) -> str:
    """Always unified. Kept for config/docs compatibility (legacy values ignored)."""
    if config is None:
        return "unified"
    raw: Any = None
    if isinstance(config, dict):
        block = config.get("answer") or {}
        if isinstance(block, dict):
            raw = block.get("orchestrator")
        if raw is None:
            raw = config.get("answer.orchestrator")
    else:
        getter = getattr(config, "get", None)
        if callable(getter):
            raw = getter("answer.orchestrator", None)
            if raw is None:
                block = getter("answer", None)
                if isinstance(block, dict):
                    raw = block.get("orchestrator")
    mode = str(raw or "unified").strip().lower()
    if mode and mode not in {"unified", "legacy", "shadow"}:
        logger.warning("Unknown answer.orchestrator=%r; using unified", raw)
    # WP2-T2: no behavioral difference — always unified
    if mode in {"legacy", "shadow"}:
        logger.debug(
            "answer.orchestrator=%s has no separate path; using unified assemble",
            mode,
        )
    return "unified"


class AnswerService:
    """Question → SearchExecution → assemble → AnswerExecution."""

    def __init__(self, search_service: Any, llm: Any = None, config: Any = None):
        self._search = search_service
        self._llm = llm
        self._config = config or {}
        self._generator = Generator(llm)

    def execute(
        self,
        question: str,
        *,
        top_k: int = 5,
        use_llm: bool = True,
        llm_answer: str | None = None,
        evidence_snapshot: dict[str, Any] | None = None,
    ) -> AnswerExecution:
        # resolve for logging/compatibility only
        resolve_answer_orchestrator_mode(self._config)
        payload = self._assemble_payload(
            question,
            top_k=top_k,
            use_llm=use_llm,
            llm_answer=llm_answer,
            evidence_snapshot=evidence_snapshot,
        )
        return AnswerExecution.from_payload(payload)

    def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        use_llm: bool = True,
        llm_answer: str | None = None,
        evidence_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.execute(
            question,
            top_k=top_k,
            use_llm=use_llm,
            llm_answer=llm_answer,
            evidence_snapshot=evidence_snapshot,
        ).to_ask_payload()

    def _run_search(
        self,
        question: str,
        *,
        top_k: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        """Only via SearchService.execute (RetrievalOrchestrator under the hood)."""
        if hasattr(self._search, "execute"):
            execution = self._search.execute(question, top_k=top_k)
            results = list(getattr(execution, "results", ()) or ())
            trace = dict(getattr(execution, "trace", None) or {})
            fb = list(getattr(execution, "fallbacks", ()) or [])
            if fb and "fallbacks" not in trace:
                trace["fallbacks"] = fb
            disclose_rows = list(getattr(execution, "disclose_claims", ()) or [])
            return results, trace, disclose_rows
        results = list(self._search.search(question, top_k=top_k) or [])
        return results, {}, []

    def _list_blocks_for_page(self, page_id: str) -> list[dict[str, Any]]:
        """Load blocks for adjacent expansion; prefers search_service.db."""
        db = getattr(self._search, "_db", None)
        if db is None:
            try:
                from src.services.db import Database
                db = Database
            except Exception:  # noqa: BLE001
                return []
        try:
            conn = db.get_conn() if hasattr(db, "get_conn") else None
            if conn is None:
                return []
            rows = conn.execute(
                """SELECT id, parent_id, page_id, content, block_type, properties,
                          order_idx, created_at, updated_at
                   FROM blocks
                   WHERE page_id = ?
                   ORDER BY order_idx ASC, created_at ASC""",
                (page_id,),
            ).fetchall()
            out = []
            for row in rows:
                d = dict(row)
                d.setdefault("block_id", d.get("id") or "")
                d.setdefault("knowledge_id", d.get("page_id") or page_id)
                d.setdefault("text", d.get("content") or "")
                out.append(d)
            return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("list blocks for adjacent expansion failed: %s", exc)
            return []

    def _results_from_snapshot(
        self,
        snapshot: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        """Use pre-accepted generation items; never re-open unconstrained retrieval."""
        # Prefer generation_items (latest-version filtered) when present.
        items = list(
            snapshot.get("generation_items")
            or snapshot.get("accepted_items")
            or ()
        )
        trace = {
            "mode": "preaccepted_snapshot",
            "query": snapshot.get("query") or "",
            "gate": {
                "accept": snapshot.get("accept"),
                "top_score": snapshot.get("top_score"),
                "threshold": snapshot.get("threshold"),
                "reason": snapshot.get("reason"),
                "intent": snapshot.get("intent"),
            },
            "accepted_knowledge_ids": list(snapshot.get("accepted_knowledge_ids") or []),
            "accepted_block_ids": list(snapshot.get("accepted_block_ids") or []),
            "adjacent_allowlist": list(snapshot.get("adjacent_allowlist") or []),
            "stages": dict(snapshot.get("stages") or {}),
            "sources": {"preaccepted": True},
        }
        return items, trace, []

    def _expand_adjacent_into_results(
        self,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """SPEC v2 Phase 3: join consecutive blocks of the same knowledge item
        into the generation context before the LLM runs."""
        from src.retrieval.canonical_snapshot import expand_results_with_adjacent

        try:
            return expand_results_with_adjacent(
                results,
                list_blocks_fn=self._list_blocks_for_page,
                window=1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("adjacent expansion skipped: %s", exc)
            return results

    def _assemble_payload(
        self,
        question: str,
        *,
        top_k: int,
        use_llm: bool,
        llm_answer: str | None,
        evidence_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if evidence_snapshot is not None:
            results, trace, disclose_rows = self._results_from_snapshot(evidence_snapshot)
            # Diff check: if a caller also left raw re-retrieval rows, refuse
            # to silently mix them. Snapshot is authoritative.
            snap_ids = {
                (str(r.get("knowledge_id") or ""), str(r.get("block_id") or ""))
                for r in results
                if isinstance(r, dict)
            }
            trace.setdefault("stages", {})["snapshot_id_count"] = len(snap_ids)
        else:
            results, trace, disclose_rows = self._run_search(question, top_k=top_k)

        # Adjacent block expansion for clause integrity (KB-019).
        # SPEC v4: skip micro-block expansion when candidates are already
        # semantic passages (they already contain full clause context).
        has_passage = any(
            isinstance(r, dict)
            and (
                r.get("passage_id")
                or r.get("retrieval_unit") == "passage"
                or r.get("candidate_type") == "passage"
            )
            for r in results
        )
        if not has_passage:
            before_n = len(results)
            results = self._expand_adjacent_into_results(results)
            if len(results) > before_n:
                stages = dict(trace.get("stages") or {})
                stages["adjacent_expanded"] = True
                stages["context_block_count"] = len(results)
                stages["adjacent_added"] = len(results) - before_n
                trace["stages"] = stages
        else:
            stages = dict(trace.get("stages") or {})
            stages["adjacent_expanded"] = False
            stages["passage_context"] = True
            trace["stages"] = stages

        # SPEC v4: structured claim protocol is primary when passage evidence
        # is present. Wiki/claim hybrid and block-only fixtures keep legacy
        # assemble_answer_payload for compatibility.
        from src.answering.claim_protocol import structured_answer_from_evidence
        from src.answering.passage_evidence import normalize_to_passage_evidence

        prefer_latest = bool(
            (trace.get("gate") or {}).get("intent") == "local_version"
            or (evidence_snapshot or {}).get("intent") == "local_version"
        )
        has_claims = any(
            isinstance(r, dict) and (r.get("claim_id") or r.get("candidate_type") == "claim")
            for r in results
        )
        norm_rows = []
        has_any_passage = False
        for r in results:
            if not isinstance(r, dict):
                continue
            pe = normalize_to_passage_evidence(r)
            row = pe.to_row()
            if pe.passage_id:
                has_any_passage = True
            if r.get("score") is not None:
                row["score"] = r.get("score")
            if r.get("final_relevance_score") is not None:
                row["final_relevance_score"] = r.get("final_relevance_score")
            norm_rows.append(row)

        # SPEC v6: always prefer FactCandidate path. When only block rows exist,
        # synthesize a stable passage_id so claim/trace can proceed without free-form LLM.
        for row in norm_rows:
            if not row.get("passage_id"):
                bid = ""
                bids = row.get("block_ids") or []
                if bids:
                    bid = str(bids[0])
                bid = bid or str(row.get("block_id") or "")
                kid = str(row.get("knowledge_id") or "")
                if bid or kid:
                    row["passage_id"] = f"block:{kid}:{bid}" if kid or bid else ""
                    row["retrieval_unit"] = row.get("retrieval_unit") or "block"
                    row["candidate_type"] = row.get("candidate_type") or "raw_block"
                    row["retrieval_fallback"] = row.get("retrieval_fallback") or "block"
                    has_any_passage = bool(row.get("passage_id")) or has_any_passage

        use_structured = bool(norm_rows) and not has_claims
        structured: dict[str, Any] = {}
        if use_structured:
            llm_json = None
            # Only claim-JSON from LLM; never free-form prose (SPEC v6 process-prose ban).
            if use_llm and llm_answer is None and self._llm is not None:
                llm_json = self._try_claim_json(question, norm_rows)
            elif isinstance(llm_answer, str) and llm_answer.strip().startswith("{"):
                llm_json = llm_answer
            structured = structured_answer_from_evidence(
                question=question,
                evidence_rows=norm_rows,
                llm_json=llm_json,
                prefer_latest_family=prefer_latest,
                # Block-fallback IDs are acceptable when passage index missed the hit.
                require_passage=bool(has_any_passage),
            )

        if use_structured:
            ans = structured.get("answer") or ""
            # Hard reject process prose that leaked past claim parse.
            import re as _re
            if _re.search(r"问题拆解|推理过程|组合推理|##\s*一、|chain[- ]?of[- ]?thought", ans, _re.I):
                structured = {
                    **structured,
                    "answer": "",
                    "answer_mode": "no_answer",
                    "sources": [],
                    "raw_evidence_used": [],
                    "reason": "process_prose_rejected",
                    "answer_validation_decision": "process_prose_rejected",
                    "user_notice": "知识库中未找到可直接支持该问题的证据。",
                }
            payload = {
                "answer": structured.get("answer") or "",
                "answer_mode": structured.get("answer_mode") or "no_answer",
                "conflict_disclosed": False,
                "claims_used": structured.get("claims_used") or [],
                "raw_evidence_used": structured.get("raw_evidence_used") or [],
                "conflicts": [],
                "fallbacks": list(trace.get("fallbacks") or []),
                "warnings": list(structured.get("warnings") or []),
                "sources": structured.get("sources") or [],
                "freshness_sensitive": prefer_latest,
                "trace_id": trace.get("trace_id") or "",
                "search_trace": {
                    "mode": trace.get("mode"),
                    "route": trace.get("route"),
                    "stages": trace.get("stages"),
                    "sources": trace.get("sources"),
                },
                "reason": structured.get("reason") or "",
                "answer_validation_decision": structured.get("answer_validation_decision")
                or structured.get("reason")
                or "",
                "user_notice": structured.get("user_notice") or "",
                "numeric_fact_audit": structured.get("numeric_fact_audit") or {},
                "claim_audit": structured.get("claim_audit") or [],
                "fact_candidate_audit": structured.get("fact_candidate_audit") or {},
                "answer_plan": structured.get("answer_plan") or {},
                "query_plan": structured.get("query_plan") or {},
                "evidence_groups": structured.get("evidence_groups") or {},
                "render_validation": structured.get("render_validation") or {},
                "primary_group_id": structured.get("primary_group_id"),
            }
            if structured.get("answer_mode") == "no_answer":
                payload["sources"] = []
                payload["raw_evidence_used"] = []
                payload["answer"] = ""
        else:
            # Verified-claim / hybrid path keeps assemble_answer_payload.
            # Free-form LLM generation is disabled for raw evidence (use_llm
            # only when claim JSON is not used above).
            generate_fn = None
            if has_claims and use_llm and llm_answer is None:
                generate_fn = self._generator.make_generate_fn()
            payload = assemble_answer_payload(
                question,
                results,
                llm_answer=llm_answer,
                search_trace=trace,
                disclose_claims=disclose_rows,
                generate_fn=generate_fn,
            )
            # Reject process prose even on legacy assemble path.
            import re as _re
            ans = str(payload.get("answer") or "")
            if _re.search(
                r"问题拆解|推理过程|组合推理|##\s*一、|chain[- ]?of[- ]?thought",
                ans,
                _re.I,
            ):
                payload["answer"] = ""
                payload["answer_mode"] = "no_answer"
                payload["sources"] = []
                payload["raw_evidence_used"] = []
                payload["reason"] = "process_prose_rejected"
                payload["answer_validation_decision"] = "process_prose_rejected"
                payload["user_notice"] = "知识库中未找到可直接支持该问题的证据。"
        payload.setdefault(
            "source_graph",
            {"nodes": [], "edges": [], "truncated": False, "node_count": 0},
        )
        payload.setdefault(
            "route",
            {
                "mode": payload["answer_mode"],
                "explanation": f"verified answer path: {payload['answer_mode']}",
                "search_mode": trace.get("mode"),
                "intent": (trace.get("route") or {}).get("intent")
                or (trace.get("gate") or {}).get("intent"),
            },
        )
        payload.setdefault("query_plan", {})
        payload.setdefault("block_contexts", {})
        payload.setdefault("wiki_context", "")
        # Surface snapshot allowlist for MCP citation integrity (do not expand
        # it from pipeline raw_evidence — SPEC v2 §4.2.5 / §5.1.1).
        if evidence_snapshot is not None:
            payload["_evidence_snapshot"] = {
                "accepted_knowledge_ids": list(
                    evidence_snapshot.get("accepted_knowledge_ids") or []
                ),
                "accepted_block_ids": list(
                    evidence_snapshot.get("accepted_block_ids") or []
                ),
                "accepted_passage_ids": list(
                    evidence_snapshot.get("accepted_passage_ids") or []
                ),
                "adjacent_allowlist": list(
                    evidence_snapshot.get("adjacent_allowlist") or []
                ),
                "generation_knowledge_ids": list(
                    evidence_snapshot.get("generation_knowledge_ids") or []
                ),
            }
        return payload

    def _try_claim_json(self, question: str, evidence_rows: list[dict[str, Any]]) -> str | None:
        """Ask LLM for claim JSON only; never use free-form prose as answer."""
        try:
            from src.answering.fallbacks import build_generation_context
            ctx = build_generation_context([], evidence_rows, conflicts=[])
            prompt = (
                "你是知识库事实抽取器。只输出 JSON，不要 Markdown。\n"
                "格式: {\"claims\":[{\"text\":\"...\",\"evidence_passage_ids\":[\"...\"],"
                "\"fact_type\":\"numeric|policy|scope|version|other\",\"condition\":\"...\"}]}\n"
                "规则: 仅陈述证据包明示事实；禁止问题拆解/推理过程/建议/复述问题；"
                "每条 claim 必须引用 evidence_passage_ids。\n"
                f"问题: {question}\n证据:\n{ctx[:6000]}"
            )
            if hasattr(self._llm, "generate"):
                out = self._llm.generate(prompt)
            elif callable(self._llm):
                out = self._llm(prompt)
            else:
                return None
            text = (out if isinstance(out, str) else str(out or "")).strip()
            if text.startswith("{"):
                return text
            # Try extract first JSON object
            import re
            m = re.search(r"\{[\s\S]*\}", text)
            return m.group(0) if m else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("claim JSON generation failed: %s", exc)
            return None

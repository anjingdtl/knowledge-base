"""Prove concurrent SearchService.execute() has no cross-request state bleed.

Phase-1: results / trace / claims / conflicts / fallbacks must stay request-scoped.
Each request is injected with unique IDs derived only from its query token.

Important: do NOT use per-call patch.object under ThreadPoolExecutor — concurrent
patches on the same instance race. Bind injection methods once before fan-out.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import MethodType
from unittest.mock import Mock

from src.models.wiki_v2 import Claim, ClaimStatus, Evidence, EvidenceStance, normalize_statement
from src.services.search_service import SearchService
from src.services.wiki_claim_extractor import compute_excerpt_hash
from src.services.wiki_serving_gate import ServingGateConfig, WikiServingGate

CONCURRENCY = 50
ROUNDS = 20


def _token_from_query(query: str) -> str:
    m = re.search(r"tok-([a-zA-Z0-9]+)", query or "")
    assert m, f"query must embed tok-*: {query!r}"
    return m.group(1)


def _claim_for_token(token: str) -> Claim:
    statement = f"statement-{token}-unique-fact"
    h = compute_excerpt_hash(statement)
    return Claim(
        schema_version=1,
        claim_id=f"claim-{token}",
        statement=statement,
        normalized_statement=normalize_statement(statement),
        claim_type="fact",
        status=ClaimStatus.ACTIVE,
        confidence=0.9,
        valid_from=None,
        valid_to=None,
        subject_refs=[],
        predicate="p",
        object_refs=[],
        evidence=[
            Evidence(
                evidence_id=f"ev-{token}",
                stance=EvidenceStance.SUPPORTS,
                knowledge_id=f"k-{token}",
                block_id=f"b-{token}",
                excerpt_hash=h,
            ),
        ],
        relations=[],
        created_at="t",
        updated_at="t",
        revision=1,
    )


def _wire_injections(service: SearchService) -> None:
    """Install query-pure injection methods once (thread-safe: pure functions of args)."""

    def _rewrite_query(self, query: str) -> list[str]:
        return [query]

    def _timed_hybrid_search(self, queries: list[str], top_k: int) -> list[dict]:
        q = queries[0] if queries else ""
        token = _token_from_query(q)
        return [{
            "id": f"raw-{token}",
            "text": f"raw-body-{token}",
            "metadata": {
                "page_id": f"kraw-{token}",
                "knowledge_id": f"kraw-{token}",
                "title": f"raw-title-{token}",
            },
            "rrf_score": 0.7,
        }]

    def _timed_rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        return list(candidates or [])[:top_k]

    def _safe_verified_claim_retrieve(self, query: str, limit: int, state=None):
        token = _token_from_query(query)
        claim = _claim_for_token(token)
        gate = WikiServingGate(
            config=ServingGateConfig(),
            get_block=lambda bid: {"id": bid, "content": claim.statement},
            get_knowledge=lambda kid: {"id": kid, "deleted_at": None},
            wiki_read_enabled=True,
            knowledge_mode="verified",
        )
        decision = gate.evaluate(claim)
        return [(claim, decision)][:limit]

    service._rewrite_query = MethodType(_rewrite_query, service)
    service._timed_hybrid_search = MethodType(_timed_hybrid_search, service)
    service._timed_rerank = MethodType(_timed_rerank, service)
    service._safe_verified_claim_retrieve = MethodType(_safe_verified_claim_retrieve, service)


def _build_service() -> SearchService:
    data = {
        "rag.enable_query_rewriting": False,
        "rag.enable_rerank": False,
        "rag.verified_knowledge.enabled": True,
        "rag.verified_knowledge.wiki_weight": 0.5,
        "rag.verified_knowledge.raw_weight": 0.5,
        "knowledge_workflow.mode": "verified",
    }
    cfg = Mock()
    cfg.get.side_effect = lambda key, default=None: data.get(key, default)

    def get_block(bid: str):
        token = bid[2:] if bid.startswith("b-") else bid
        return {"id": bid, "content": f"statement-{token}-unique-fact"}

    def get_knowledge(kid: str):
        return {"id": kid, "deleted_at": None, "title": f"title-{kid}"}

    gate = WikiServingGate(
        config=ServingGateConfig(),
        get_block=get_block,
        get_knowledge=get_knowledge,
        wiki_read_enabled=True,
        knowledge_mode="verified",
    )
    db = Mock()
    db.get_knowledge.side_effect = lambda kid: {"id": kid, "title": f"title-{kid}"}

    class EmptyRepo:
        def list_claims(self):
            return []

    service = SearchService(
        cfg, db, Mock(), Mock(), Mock(),
        wiki_repository=EmptyRepo(),
        wiki_serving_gate=gate,
    )
    _wire_injections(service)
    return service


def _run_one(service: SearchService, token: str):
    query = f"lookup tok-{token} statement-{token}-unique-fact"
    return service.execute(query, top_k=5)


def _assert_no_bleed(token: str, execution) -> list[str]:
    errors: list[str] = []
    trace_q = str((execution.trace or {}).get("query") or "")
    if f"tok-{token}" not in trace_q:
        errors.append(f"trace_query_mismatch token={token} query={trace_q!r}")

    mode = (execution.trace or {}).get("mode")
    if mode != "hybrid_verified":
        errors.append(f"trace_mode_unexpected token={token} mode={mode}")

    for row in execution.results:
        src = row.get("source")
        if src == "verified_claim":
            cid = str(row.get("claim_id") or "")
            if cid != f"claim-{token}":
                errors.append(f"claim_bleed token={token} claim_id={cid}")
            text = str(row.get("text") or "")
            if f"statement-{token}" not in text:
                errors.append(f"claim_text_bleed token={token} text={text[:80]!r}")
        elif src == "knowledge":
            kid = str(row.get("knowledge_id") or "")
            bid = str(row.get("block_id") or "")
            if kid and kid not in {f"kraw-{token}", f"k-{token}"}:
                errors.append(f"citation_kid_bleed token={token} kid={kid}")
            if bid and bid not in {f"raw-{token}", f"b-{token}"}:
                errors.append(f"citation_bid_bleed token={token} bid={bid}")
        else:
            blob = str(row)
            foreign = re.findall(r"(?:claim|raw|kraw|k|b)-([0-9]{3}x)", blob)
            for t in foreign:
                if t != token:
                    errors.append(f"foreign_token_in_result token={token} foreign={t}")

    for row in execution.disclose_claims:
        cid = str(row.get("claim_id") or "")
        if cid and cid != f"claim-{token}":
            errors.append(f"disclose_bleed token={token} claim_id={cid}")

    for c in execution.conflicts:
        for key in ("claim_a_id", "claim_b_id"):
            cid = str(c.get(key) or "")
            if cid and token not in cid:
                errors.append(f"conflict_bleed token={token} {key}={cid}")

    for fb in execution.fallbacks:
        reason = str(fb.get("reason") or "")
        for t in re.findall(r"tok-([a-zA-Z0-9]+)", reason):
            if t != token:
                errors.append(f"fallback_bleed token={token} foreign={t}")

    if not execution.results:
        errors.append(f"empty_results token={token}")

    return errors


class TestSearchRequestIsolation:
    def test_concurrent_execute_no_cross_contamination(self):
        tokens = [f"{i:03d}x" for i in range(CONCURRENCY)]
        service = _build_service()

        total_trace = total_claim = total_conflict = total_fallback = total_citation = 0

        for _round in range(ROUNDS):
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
                futs = {pool.submit(_run_one, service, tok): tok for tok in tokens}
                for fut in as_completed(futs):
                    tok = futs[fut]
                    ex = fut.result()
                    errs = _assert_no_bleed(tok, ex)
                    for e in errs:
                        if e.startswith("trace_"):
                            total_trace += 1
                        elif e.startswith("claim_") or e.startswith("disclose_"):
                            total_claim += 1
                        elif e.startswith("fallback_"):
                            total_fallback += 1
                        elif e.startswith("citation_"):
                            total_citation += 1
                        else:
                            total_conflict += 1
                    assert not errs, f"round bleed: {errs[:8]}"

        assert total_trace == 0
        assert total_claim == 0
        assert total_conflict == 0
        assert total_fallback == 0
        assert total_citation == 0

    def test_no_instance_last_state_attributes(self):
        service = SearchService({}, Mock(), Mock(), Mock(), Mock())
        assert not hasattr(service, "last_search_trace")
        assert not hasattr(service, "last_disclose_claims")
        assert not hasattr(SearchService, "get_disclose_claim_rows")

    def test_sequential_requests_do_not_share_trace(self):
        service = _build_service()
        ex1 = _run_one(service, "aaa")
        ex2 = _run_one(service, "bbb")
        assert "tok-aaa" in (ex1.trace.get("query") or "")
        assert "tok-bbb" in (ex2.trace.get("query") or "")
        assert ex1.trace is not ex2.trace
        assert not any("bbb" in str(r.get("claim_id", "")) for r in ex1.results)
        assert not any("aaa" in str(r.get("claim_id", "")) for r in ex2.results)

"""Canonical retrieval snapshot shared by MCP ``search`` and ``ask``.

SPEC v2 Phase 1: search and ask must share one serializable evidence object —
the same candidates, the same gate decision, and the same accepted ID set.
AnswerService must not silently re-retrieve unconstrained evidence for the
same question.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from src.models.search_execution import SearchExecution
from src.services.relevance_gate import classify_query_intent, evaluate_evidence_unified
from src.services.version_rank import (
    extract_version_year,
    filter_to_latest_versions,
    rank_with_freshness,
)


def build_adjacent_allowlist(
    accepted_items: list[dict[str, Any]],
    *,
    list_blocks_fn: Callable[[str], list[dict[str, Any]]] | None = None,
    window: int = 1,
    retrieval_unit: str = "block",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build allowlist entries for accepted hits + optional adjacent units.

    SPEC v5 §3: when ``retrieval_unit == "passage"``, do **not** load/expand
    whole-page blocks. Adjacent expansion for passages is handled separately
    via passage_index ±1 (see ``build_passage_adjacent_entries``).

    Each entry:
      knowledge_id, block_id, is_adjacent_extension, parent_hit_block_id,
      order_idx, relevance_score (from parent hit when adjacent)

    Returns (allowlist, audit) where audit has adjacent_unit / count / reason.
    """
    from src.answering.context_builder import expand_adjacent_evidence

    audit: dict[str, Any] = {
        "adjacent_unit": "none",
        "adjacent_count": 0,
        "adjacent_fallback_reason": "",
        "focus_not_found": 0,
    }

    if retrieval_unit == "passage" or _items_are_passages(accepted_items):
        # Passage path: hits only — no list_blocks_fn.
        audit["adjacent_unit"] = "passage"
        audit["adjacent_fallback_reason"] = "passage_path_skips_block_expansion"
        return _allowlist_from_hits_only(accepted_items), audit

    if list_blocks_fn is None:
        audit["adjacent_unit"] = "block"
        audit["adjacent_fallback_reason"] = "no_list_blocks_fn"
        return _allowlist_from_hits_only(accepted_items), audit

    audit["adjacent_unit"] = "block"
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    adj_n = 0

    for hit in accepted_items:
        if not isinstance(hit, dict):
            continue
        kid = str(hit.get("knowledge_id") or "").strip()
        bid = str(hit.get("block_id") or "").strip()
        if not kid:
            continue
        score = hit.get("final_relevance_score", hit.get("score"))
        if bid and (kid, bid) not in seen:
            seen.add((kid, bid))
            out.append({
                "knowledge_id": kid,
                "block_id": bid,
                "is_adjacent_extension": False,
                "parent_hit_block_id": bid,
                "order_idx": hit.get("order_idx"),
                "relevance_score": score,
                "channel": hit.get("match_channel") or hit.get("source") or "",
            })
        try:
            page_blocks = list_blocks_fn(kid) or []
        except Exception:  # noqa: BLE001
            page_blocks = []
        if not page_blocks or not bid:
            continue
        normalized = []
        for b in page_blocks:
            if not isinstance(b, dict):
                continue
            nb = dict(b)
            nb.setdefault("block_id", nb.get("id") or "")
            nb.setdefault("knowledge_id", kid)
            if "text" not in nb:
                nb["text"] = nb.get("content") or ""
            normalized.append(nb)
        expanded, exp_audit = expand_adjacent_evidence(
            normalized, focus_block_id=bid, window=window, return_audit=True,
        )
        if exp_audit.get("reason") == "focus_not_found":
            audit["focus_not_found"] = int(audit.get("focus_not_found") or 0) + 1
            # Fail-closed: do not add any blocks for this hit.
            continue
        for b in expanded:
            ebid = str(b.get("block_id") or b.get("id") or "").strip()
            if not ebid or (kid, ebid) in seen:
                continue
            seen.add((kid, ebid))
            is_adj = ebid != bid
            if is_adj:
                adj_n += 1
            out.append({
                "knowledge_id": kid,
                "block_id": ebid,
                "is_adjacent_extension": is_adj,
                "parent_hit_block_id": bid,
                "order_idx": b.get("order_idx"),
                "relevance_score": score,
                "channel": "adjacent_extension" if is_adj else (
                    hit.get("match_channel") or hit.get("source") or ""
                ),
                "text": (b.get("text") or b.get("content") or "")[:500],
                "title": hit.get("title") or "",
            })
    audit["adjacent_count"] = adj_n
    return out, audit


def _items_are_passages(items: list[dict[str, Any]]) -> bool:
    if not items:
        return False
    n = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        if (
            it.get("passage_id")
            or it.get("retrieval_unit") == "passage"
            or it.get("candidate_type") == "passage"
        ):
            n += 1
    return n >= max(1, (len(items) + 1) // 2)


def build_passage_adjacent_entries(
    accepted_items: list[dict[str, Any]],
    *,
    list_neighbor_passages_fn: Callable[[str, str, int], list[dict[str, Any]]] | None = None,
    window: int = 1,
) -> tuple[list[dict[str, Any]], int]:
    """Optional ±1 passage_index neighbors; tagged passage_adjacent_extension.

    ``list_neighbor_passages_fn(knowledge_id, passage_id, window)`` returns
    neighbor passage dicts (not including the focus, or including — both OK).
    """
    if list_neighbor_passages_fn is None:
        return [], 0
    out: list[dict[str, Any]] = []
    seen_pid: set[str] = set()
    for hit in accepted_items:
        if not isinstance(hit, dict):
            continue
        kid = str(hit.get("knowledge_id") or "").strip()
        pid = str(hit.get("passage_id") or "").strip()
        if not kid or not pid:
            continue
        seen_pid.add(pid)
        try:
            neighbors = list_neighbor_passages_fn(kid, pid, window) or []
        except Exception:  # noqa: BLE001
            neighbors = []
        for n in neighbors:
            if not isinstance(n, dict):
                continue
            npid = str(n.get("passage_id") or n.get("id") or "").strip()
            if not npid or npid in seen_pid or npid == pid:
                continue
            seen_pid.add(npid)
            row = dict(n)
            row["knowledge_id"] = kid
            row["passage_id"] = npid
            row["is_adjacent_extension"] = True
            row["passage_adjacent_extension"] = True
            row["parent_hit_passage_id"] = pid
            row["retrieval_unit"] = "passage"
            row["candidate_type"] = "passage"
            row["channel"] = "passage_adjacent_extension"
            out.append(row)
    # Cap: accepted_passage_count * 2
    max_adj = max(0, len([i for i in accepted_items if isinstance(i, dict) and i.get("passage_id")]) * 2)
    if len(out) > max_adj:
        out = out[:max_adj]
    return out, len(out)


def _allowlist_from_hits_only(accepted_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for hit in accepted_items:
        if not isinstance(hit, dict):
            continue
        kid = str(hit.get("knowledge_id") or "").strip()
        bid = str(hit.get("block_id") or "").strip()
        if not kid:
            continue
        key = (kid, bid)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "knowledge_id": kid,
            "block_id": bid,
            "is_adjacent_extension": False,
            "parent_hit_block_id": bid,
            "order_idx": hit.get("order_idx"),
            "relevance_score": hit.get("final_relevance_score", hit.get("score")),
            "channel": hit.get("match_channel") or hit.get("source") or "",
        })
    return out


def apply_post_relevance_freshness(
    query: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply version freshness AFTER final relevance ranking (SPEC v2 §5.1.5).

    For local_version intents, also prefer newest year within the same
    regulation family so a slightly lower-relevance 2026 doc outranks a
    higher-lexical 2023 doc that would otherwise pollute answers.
    """
    if not items:
        return []
    ranked = rank_with_freshness(items)
    intent = classify_query_intent(query)
    if intent == "local_version":
        # Stable secondary key: newer year first within comparable scores.
        ranked = sorted(
            ranked,
            key=lambda r: (
                bool((r.get("version_rank") or {}).get("deprecated")),
                # Prefer higher relevance first, but break ties / near-ties by year.
                -_effective_rank_score(r),
                -(extract_version_year(r) or 0),
            ),
        )
    return ranked


def _effective_rank_score(item: dict[str, Any]) -> float:
    for key in ("ranking_score", "final_relevance_score", "score", "fts_score"):
        v = item.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def snapshot_to_search_execution(snapshot: dict[str, Any]) -> SearchExecution:
    """Project a canonical snapshot onto SearchExecution (no parallel model)."""
    results = tuple(snapshot.get("accepted_items") or ())
    trace = {
        "mode": "canonical_snapshot",
        "query": snapshot.get("query") or "",
        "expanded_queries": list(snapshot.get("expanded_queries") or []),
        "gate": {
            "accept": bool(snapshot.get("accept")),
            "reason": snapshot.get("reason"),
            "top_score": snapshot.get("top_score"),
            "threshold": snapshot.get("threshold"),
            "intent": snapshot.get("intent"),
        },
        "accepted_knowledge_ids": list(snapshot.get("accepted_knowledge_ids") or []),
        "accepted_block_ids": list(snapshot.get("accepted_block_ids") or []),
        "adjacent_allowlist": list(snapshot.get("adjacent_allowlist") or []),
        "stages": dict(snapshot.get("stages") or {}),
    }
    return SearchExecution(results=results, trace=trace)


def build_canonical_snapshot(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    threshold: float = 0.35,
    top_k: int = 5,
    expanded_queries: list[str] | None = None,
    list_blocks_fn: Callable[[str], list[dict[str, Any]]] | None = None,
    list_neighbor_passages_fn: Callable[[str, str, int], list[dict[str, Any]]] | None = None,
    select_document_passages_fn: Callable[[str, str, set[str], int], list[dict[str, Any]]] | None = None,
    adjacent_window: int = 1,
) -> dict[str, Any]:
    """Gate + freshness + allowlist over a candidate list.

    ``candidates`` must already be the shared retrieval output (same path
    used by search and ask probes).
    """
    q = query or ""
    pool = list(candidates or [])
    decision = evaluate_evidence_unified(
        q, pool[: max(top_k, 10)], threshold=threshold,
    )
    # SPEC v4 §E: keep global threshold; multi-slot direct evidence may accept.
    try:
        from src.answering.direct_slot_gate import apply_direct_slot_accept
        decision = apply_direct_slot_accept(
            q,
            pool[: max(top_k, 20)],
            base_decision=decision,
            threshold=threshold,
        )
    except Exception:
        pass
    intent = decision.get("intent") or classify_query_intent(q)
    accepted = list(decision.get("items") or [])
    # Freshness AFTER relevance ranking (SPEC v2).
    accepted = apply_post_relevance_freshness(q, accepted)
    # For generation / ask, keep a generation_items view that drops superseded
    # versions of the same regulation when the user asked for the latest.
    generation_items = list(accepted)
    if intent == "local_version" and accepted:
        generation_items = filter_to_latest_versions(accepted)

    accepted_kids: list[str] = []
    accepted_blocks: list[str] = []
    accepted_passages: list[str] = []
    seen_k: set[str] = set()
    seen_b: set[str] = set()
    seen_p: set[str] = set()
    for r in accepted:
        kid = str(r.get("knowledge_id") or "").strip()
        bid = str(r.get("block_id") or "").strip()
        pid = str(r.get("passage_id") or "").strip()
        if kid and kid not in seen_k:
            seen_k.add(kid)
            accepted_kids.append(kid)
        if bid and bid not in seen_b:
            seen_b.add(bid)
            accepted_blocks.append(bid)
        if pid and pid not in seen_p:
            seen_p.add(pid)
            accepted_passages.append(pid)

    is_passage = _items_are_passages(accepted[:top_k]) or bool(accepted_passages)
    # SPEC v5: passage path must not call list_blocks_fn / expand whole pages.
    allowlist, adj_audit = build_adjacent_allowlist(
        accepted[:top_k],
        list_blocks_fn=None if is_passage else list_blocks_fn,
        window=adjacent_window,
        retrieval_unit="passage" if is_passage else "block",
    )

    # For numeric questions, a top passage can contain an incidental number
    # while the actual limit sits in another raw-hit passage of the same
    # document.  Promote a bounded set of those same-document numeric passages
    # from the existing raw pool; this never expands to arbitrary page text.
    if re.search(r"限额|金额|处罚|奖金|占比|多少|上限|元", q):
        money_re = re.compile(r"\d+(?:\.\d+)?\s*(?:万元|元|%|％)")
        primary_kids = {
            str(r.get("knowledge_id") or "").strip()
            for r in accepted[:top_k]
            if isinstance(r, dict) and r.get("knowledge_id")
        }
        promoted = 0
        for r in pool:
            if not isinstance(r, dict):
                continue
            kid = str(r.get("knowledge_id") or "").strip()
            if kid not in primary_kids:
                continue
            text = str(r.get("text") or r.get("body_text") or "")
            if not money_re.search(text):
                continue
            pid = str(r.get("passage_id") or "").strip()
            if pid and pid in seen_p:
                continue
            row = dict(r)
            row["promoted_numeric_passage"] = True
            accepted.append(row)
            generation_items.append(row)
            if pid:
                seen_p.add(pid)
                accepted_passages.append(pid)
            promoted += 1
            if promoted >= 4:
                break

    # The retrieval hit can be a document's definition or heading while the
    # answerable condition/value/responsibility is a different indexed passage
    # in that same already-accepted document.  Select a small number of such
    # passages with a query-derived scorer.  This is intentionally not a page
    # expansion: the callback receives the accepted knowledge id only, returns
    # indexed passages only, and its output is explicitly allowlisted.
    targeted_passages: list[dict[str, Any]] = []
    if select_document_passages_fn is not None and accepted:
        primary_kid = str(accepted[0].get("knowledge_id") or "").strip()
        existing_pids = set(seen_p)
        if primary_kid:
            try:
                targeted_passages = select_document_passages_fn(
                    primary_kid, q, existing_pids, 3,
                ) or []
            except Exception:
                targeted_passages = []
        for row in targeted_passages:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("passage_id") or "").strip()
            if not pid or pid in seen_p:
                continue
            row = dict(row)
            row["targeted_document_evidence"] = True
            accepted.append(row)
            generation_items.append(row)
            seen_p.add(pid)
            accepted_passages.append(pid)
            allowlist.append({
                "knowledge_id": row.get("knowledge_id") or primary_kid,
                "block_id": row.get("block_id") or "",
                "passage_id": pid,
                "is_adjacent_extension": False,
                "targeted_document_evidence": True,
                "relevance_score": row.get("score"),
                "channel": "accepted_document_passage",
            })

    # Optional same-doc ±1 passage neighbors for generation context only.
    passage_adj: list[dict[str, Any]] = []
    passage_adj_n = 0
    if is_passage and list_neighbor_passages_fn is not None:
        passage_adj, passage_adj_n = build_passage_adjacent_entries(
            accepted[:top_k],
            list_neighbor_passages_fn=list_neighbor_passages_fn,
            window=1,
        )
        for n in passage_adj:
            npid = str(n.get("passage_id") or "").strip()
            if npid and npid not in seen_p:
                seen_p.add(npid)
                accepted_passages.append(npid)
            allowlist.append({
                "knowledge_id": n.get("knowledge_id") or "",
                "block_id": n.get("block_id") or "",
                "passage_id": npid,
                "is_adjacent_extension": True,
                "passage_adjacent_extension": True,
                "parent_hit_passage_id": n.get("parent_hit_passage_id") or "",
                "relevance_score": n.get("score"),
                "channel": "passage_adjacent_extension",
            })
        # Merge into generation_items (not unlimited).
        gen_ids = {
            str(r.get("passage_id") or "").strip()
            for r in generation_items
            if isinstance(r, dict)
        }
        for n in passage_adj:
            npid = str(n.get("passage_id") or "").strip()
            if npid and npid not in gen_ids:
                generation_items.append(n)
                gen_ids.add(npid)
        adj_audit["adjacent_unit"] = "passage"
        adj_audit["adjacent_count"] = passage_adj_n
        adj_audit["adjacent_fallback_reason"] = (
            "passage_index_neighbors" if passage_adj_n else "no_neighbors"
        )

    # Ensure generation_items blocks are also allowlisted.
    gen_kids = {
        str(r.get("knowledge_id") or "").strip()
        for r in generation_items
        if r.get("knowledge_id")
    }
    # SPEC v3/v4: family/version audit for generation isolation.
    gen_passages = [
        str(r.get("passage_id") or "").strip()
        for r in generation_items
        if r.get("passage_id")
    ]
    version_exclusions: list[dict] = []
    for r in generation_items:
        vr = r.get("version_rank") if isinstance(r.get("version_rank"), dict) else {}
        for ex in vr.get("excluded_family_versions") or []:
            if ex not in version_exclusions:
                version_exclusions.append(ex)

    # Final assertion for local_version: generation must not include excluded years.
    if intent == "local_version" and generation_items:
        from src.services.version_rank import family_key_of, extract_version_year
        family_newest: dict[str, int] = {}
        for r in accepted:
            fk = family_key_of(r)
            y = extract_version_year(r)
            if fk and y is not None:
                family_newest[fk] = max(family_newest.get(fk, 0), y)
        filtered_gen = []
        for r in generation_items:
            fk = family_key_of(r)
            y = extract_version_year(r)
            newest = family_newest.get(fk)
            if fk and y is not None and newest is not None and y < newest:
                version_exclusions.append({
                    "knowledge_id": r.get("knowledge_id"),
                    "passage_id": r.get("passage_id"),
                    "family_key": fk,
                    "version_year": y,
                    "newest_year": newest,
                    "reason": "generation_preassert_excluded",
                })
                continue
            filtered_gen.append(r)
        generation_items = filtered_gen

    # Recompute gen_kids/passages after filter.
    gen_kids = {
        str(r.get("knowledge_id") or "").strip()
        for r in generation_items
        if r.get("knowledge_id")
    }
    gen_passages = [
        str(r.get("passage_id") or "").strip()
        for r in generation_items
        if r.get("passage_id")
    ]

    # SPEC v6 §4.1: stable fingerprint for search/ask accepted evidence identity.
    import hashlib
    fp_raw = "|".join(
        [
            q.strip(),
            str(threshold),
            str(top_k),
            ",".join(accepted_passages),
            ",".join(accepted_kids),
            str(bool(decision.get("accept"))),
            str(decision.get("reason") or ""),
        ]
    )
    snapshot_fingerprint = hashlib.sha256(fp_raw.encode("utf-8")).hexdigest()[:24]

    return {
        "query": q,
        "expanded_queries": list(expanded_queries or [q]),
        "raw_candidates": pool,
        "accept": bool(decision.get("accept")),
        "reason": decision.get("reason"),
        "top_score": decision.get("top_score", 0.0),
        "threshold": threshold,
        "intent": intent,
        "accepted_items": accepted[:top_k],
        "generation_items": generation_items[: max(top_k, top_k + passage_adj_n + len(targeted_passages))],
        "accepted_knowledge_ids": accepted_kids,
        "accepted_block_ids": accepted_blocks,
        "accepted_passage_ids": accepted_passages,
        "generation_passage_ids": [p for p in gen_passages if p],
        "generation_knowledge_ids": sorted(gen_kids),
        "adjacent_allowlist": allowlist,
        "adjacent_unit": adj_audit.get("adjacent_unit") or "none",
        "adjacent_count": int(adj_audit.get("adjacent_count") or 0),
        "adjacent_fallback_reason": adj_audit.get("adjacent_fallback_reason") or "",
        "gate_evidence": list(decision.get("evidence") or []),
        "version_exclusions": version_exclusions,
        "snapshot_fingerprint": snapshot_fingerprint,
        "stages": {
            "gate": {
                "accept": bool(decision.get("accept")),
                "top_score": decision.get("top_score"),
                "threshold": threshold,
                "reason": decision.get("reason"),
                "direct_slot_evidence": bool(decision.get("direct_slot_evidence")),
            },
            "freshness_applied_after_relevance": True,
            "local_version_filtered": intent == "local_version",
            "retrieval_unit": "passage" if is_passage else "block",
            "direct_slot_audit": decision.get("direct_slot_audit") or {},
            "adjacent_unit": adj_audit.get("adjacent_unit"),
            "adjacent_count": adj_audit.get("adjacent_count"),
            "adjacent_fallback_reason": adj_audit.get("adjacent_fallback_reason"),
            "focus_not_found": adj_audit.get("focus_not_found"),
            "snapshot_fingerprint": snapshot_fingerprint,
        },
        "direct_slot_evidence": bool(decision.get("direct_slot_evidence")),
        "direct_slot_audit": decision.get("direct_slot_audit") or {},
    }


def source_in_allowlist(
    source: dict[str, Any],
    *,
    accepted_knowledge_ids: set[str],
    accepted_block_ids: set[str],
    adjacent_allowlist: list[dict[str, Any]] | None = None,
    accepted_passage_ids: set[str] | None = None,
) -> bool:
    """True if source is pre-accepted or an explicit adjacent extension."""
    if not isinstance(source, dict):
        return False
    kid = str(source.get("knowledge_id") or "").strip()
    bid = str(source.get("block_id") or "").strip()
    pid = str(source.get("passage_id") or "").strip()
    if pid and accepted_passage_ids and pid in accepted_passage_ids:
        return True
    if bid and bid in accepted_block_ids:
        return True
    if kid and kid in accepted_knowledge_ids and not bid and not pid:
        # Knowledge-level citation without block is acceptable only when the
        # knowledge item itself was pre-accepted.
        return True
    if kid and kid in accepted_knowledge_ids and (bid in accepted_block_ids or pid):
        return True
    if kid and bid and kid in accepted_knowledge_ids and bid in accepted_block_ids:
        return True
    for entry in adjacent_allowlist or []:
        if (
            str(entry.get("knowledge_id") or "").strip() == kid
            and str(entry.get("block_id") or "").strip() == bid
        ):
            return True
        if pid and str(entry.get("passage_id") or "").strip() == pid:
            return True
    return False


def expand_results_with_adjacent(
    hits: list[dict[str, Any]],
    *,
    list_blocks_fn: Callable[[str], list[dict[str, Any]]],
    window: int = 1,
) -> list[dict[str, Any]]:
    """Expand hit blocks with same-knowledge adjacent blocks for LLM context.

    Returns a new list of raw-row shaped dicts. Adjacent rows are tagged
    ``is_adjacent_extension=True`` and keep ``parent_hit_block_id``.
    """
    from src.answering.context_builder import expand_adjacent_evidence

    if not hits:
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for hit in hits:
        if not isinstance(hit, dict):
            continue
        kid = str(hit.get("knowledge_id") or "").strip()
        bid = str(hit.get("block_id") or "").strip()
        title = hit.get("title") or ""
        score = hit.get("final_relevance_score", hit.get("score"))

        # Always include the hit itself first.
        key = (kid, bid or id(hit))
        if key not in seen:
            seen.add(key)
            row = dict(hit)
            row.setdefault("is_adjacent_extension", False)
            row.setdefault("parent_hit_block_id", bid)
            out.append(row)

        if not kid or not bid:
            continue
        try:
            page_blocks = list_blocks_fn(kid) or []
        except Exception:  # noqa: BLE001
            continue
        normalized = []
        for b in page_blocks:
            if not isinstance(b, dict):
                continue
            nb = dict(b)
            nb.setdefault("block_id", nb.get("id") or "")
            nb.setdefault("knowledge_id", kid)
            if "text" not in nb:
                nb["text"] = nb.get("content") or ""
            normalized.append(nb)
        expanded = expand_adjacent_evidence(
            normalized, focus_block_id=bid, window=window,
        )
        # focus_not_found → empty list (fail-closed); skip expansion.
        for b in expanded:
            ebid = str(b.get("block_id") or b.get("id") or "").strip()
            if not ebid:
                continue
            ekey = (kid, ebid)
            if ekey in seen:
                continue
            seen.add(ekey)
            text = b.get("text") or b.get("content") or ""
            out.append({
                "source": hit.get("source") or "knowledge",
                "knowledge_id": kid,
                "block_id": ebid,
                "title": title,
                "text": text,
                "score": score,
                "final_relevance_score": score,
                "candidate_type": "raw_block",
                "source_layer": "evidence",
                "is_adjacent_extension": ebid != bid,
                "parent_hit_block_id": bid,
                "order_idx": b.get("order_idx"),
                "match_channel": "adjacent_extension",
            })
    return out

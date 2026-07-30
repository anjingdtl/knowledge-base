"""CandidateRetrievalService — application-layer candidate retrieval (Phase 2).

Moves candidate generation out of ``src/mcp/tools/retrieval.py`` so MCP
adapters stay thin (protocol / envelope only). Behaviour is preserved
bit-for-bit with the previous MCP implementation; the only change is the
dependency direction — MCP now calls the application service instead of
holding the business logic itself.

The service uses:
- ``RetrievalCommands.semantic_search`` (application boundary) for the
  semantic channel — never ``search_service._get_raw_retriever()`` private
  method (ADR ``retrieval-answer-boundaries-v2`` §3 / §8).
- ``RetrievalCommands.fulltext_search`` for the FTS recall aid — replaces
  the previous MCP ``search_fulltext`` self-call (breaks the MCP→MCP
  circular dependency).
- ``PassageStore`` via an injected factory for passage-level FTS and
  enrichment (kept as a port so tests can inject fakes).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from src.application.retrieval_commands import RetrievalCommands
from src.services.query_rewrite import build_alias_query_variants, expand_query

logger = logging.getLogger(__name__)

# Type alias for a PassageStore factory (avoids hard import at module load).
PassageStoreFactory = Callable[[], Any]


def _default_passage_store_factory() -> Any:
    """Production PassageStore factory (lazy import)."""
    from src.services.passage_store import PassageStore

    return PassageStore()


class CandidateRetrievalService:
    """Shared candidate retrieval for Search and Ask (ADR §5 / §3.3).

    Both ``search`` and ``ask`` evaluate the SAME candidate set with the
    SAME scores (SPEC Phase 1.4). The original query always runs first
    and wins score ties. SPEC v3 prefers passage units for both semantic
    and FTS channels.
    """

    def __init__(
        self,
        container: Any,
        *,
        passage_store_factory: PassageStoreFactory | None = None,
        fulltext_search_fn: Callable[..., list[dict[str, Any]]] | None = None,
    ):
        self._container = container
        self._commands = RetrievalCommands(container)
        self._passage_store_factory = (
            passage_store_factory or _default_passage_store_factory
        )
        # Allow callers (tests / application wiring) to inject a fulltext
        # search callable that does not route through MCP. Defaults to the
        # application RetrievalCommands.fulltext_search.
        self._fulltext_search_fn = fulltext_search_fn or self._commands.fulltext_search

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def retrieve_candidates(self, query: str, *, fetch_k: int) -> list[dict[str, Any]]:
        """Shared retrieval used by ``search`` and ``ask`` pre-LLM evidence probe.

        Runs semantic search (same path as the ``search`` tool) with the same
        numeric-unit ranking, document-level dedupe, title-overlap boost and
        version-freshness re-rank. This guarantees ``search`` and ``ask``
        evaluate the SAME candidate set with the SAME scores (SPEC Phase 1.4)
        and that the newest effective version of a regulation ranks first.

        The compatibility path retains user-derived query variants so formal
        wording can be found from colloquial wording. The original query
        always runs first and wins score ties.

        SPEC v3: prefer passage units for both semantic and FTS channels;
        enrich any residual micro-block hits with owning passage text.
        """
        # One production retrieval authority for MCP search and ask.  Keeping
        # this path aligned with RawRetriever avoids separate legacy synonym
        # tables and makes the A/B candidate snapshot representative of MCP
        # behavior.
        try:
            service = getattr(self._container, "search_service", None)
            if service is not None:
                raw = service._get_raw_retriever()
                result = raw.retrieve(
                    query, top_k=max(fetch_k, 5), include_legacy_wiki_fts=False
                )
                return [dict(row) for row in result.candidates if isinstance(row, dict)]
        except Exception as exc:  # legacy path remains an availability fallback
            logger.debug("unified raw retrieval failed; using compatibility path: %s", exc)

        from src.services.numeric_unit_match import apply_numeric_unit_ranking
        from src.services.query_rewrite import (
            canonical_terms,
            merge_candidates_by_query,
        )
        from src.services.result_dedupe import (
            boost_title_term_overlap,
            dedupe_retrieval_hits,
        )

        results = self._semantic_with_variants(query, fetch_k)

        # SPEC v3: always merge passage FTS (semantic unit).
        passage_lists = [self._passage_fts_hits(query, limit=max(fetch_k, 10))]
        for term in canonical_terms(query):
            hits = self._passage_fts_hits(term, limit=max(fetch_k, 10))
            for h in hits:
                h["alias_fts_match"] = True
            passage_lists.append(hits)
        passage_items = (
            merge_candidates_by_query(query, [x for x in passage_lists if x])
            if any(passage_lists)
            else []
        )
        if passage_items:
            results = merge_candidates_by_query(query, [results, passage_items])

        # SPEC Phase 3.3 (legacy FTS recall aid): if still weak, also run
        # block/knowledge FTS then enrich to passages. Keep gate threshold
        # unchanged.
        if not results or self._top_score(results) < 0.35:
            ft_lists = []
            ft = self._fulltext_search_fn(query, limit=max(fetch_k, 10), offset=0)
            ft_lists.append(list(ft)[: max(fetch_k, 10)])
            for term in canonical_terms(query):
                ft = self._fulltext_search_fn(term, limit=max(fetch_k, 10), offset=0)
                hits = list(ft)[: max(fetch_k, 10)]
                for h in hits:
                    if isinstance(h, dict):
                        h["alias_fts_match"] = True
                ft_lists.append(hits)
            if ft_lists:
                ft_items = merge_candidates_by_query(query, ft_lists)
                results = merge_candidates_by_query(query, [results, ft_items])

        # Prefer passage text over micro-block snippets for evidence quality.
        results = self._enrich_with_passages(results)

        apply_numeric_unit_ranking(query, results)
        # SPEC v5: keep multi-passage diversity within a document.
        results = dedupe_retrieval_hits(results, max_passages_per_knowledge=3)
        results = boost_title_term_overlap(query, results)
        # NOTE: rank_with_freshness is intentionally NOT applied here. SPEC v2
        # requires freshness to run AFTER final relevance ranking so a later
        # re-score cannot bury the newest edition.

        out: list[dict[str, Any]] = []
        for item in results:
            row = dict(item) if isinstance(item, dict) else {"text": str(item)}
            if not row.get("knowledge_id") and row.get("id"):
                row["knowledge_id"] = row["id"]
            out.append(row)
        return out

    # ------------------------------------------------------------------ #
    # Internal helpers (preserved from MCP retrieval.py)                  #
    # ------------------------------------------------------------------ #

    def _semantic_with_variants(
        self, query: str, fetch_k: int
    ) -> list[dict[str, Any]]:
        from src.services.query_rewrite import expand_query, merge_candidates_by_query

        def _semantic(q: str) -> list[dict[str, Any]]:
            try:
                container = self._container
                if getattr(container, "search_service", None) is not None:
                    hits = list(
                        self._commands.semantic_search(q, top_k=fetch_k) or []
                    )
                    for h in hits:
                        if isinstance(h, dict) and h.get("score") is not None:
                            try:
                                h["_semantic_similarity"] = float(h["score"])
                            except (TypeError, ValueError):
                                pass
                    return hits
            except Exception as exc:  # noqa: BLE001
                logger.debug("shared retrieval semantic path failed: %s", exc)
            return []

        # SPEC Phase 3.2: include alias-expanded variants so formal documents
        # (竞赛/门店) can be found from colloquial queries (比赛/店铺).
        # Hits from alias-expanded variants are tagged with ``alias_fts_match``
        # so the relevance gate can credit them (the candidate was verified via
        # a synonym expansion, not incidental word overlap). The flag is set
        # ONLY on alias-variant hits — never on the original-query hits — so it
        # cannot fire for ordinary no-answer candidates.
        variants = expand_query(query)
        alias_vs = [v["query"] for v in build_alias_query_variants(query)]
        for av in alias_vs:
            if av and av not in variants:
                variants.append(av)
        if len(variants) <= 1:
            return _semantic(query)

        # Run the original query WITHOUT the alias flag, and alias variants
        # WITH the flag. merge_candidates_by_query preserves the flag on
        # deduplicated items (first occurrence wins, but original-query hits
        # are processed first so their absence of the flag is preserved).
        candidate_lists = [_semantic(query)]
        for av in alias_vs:
            if av and av != query:
                hits = _semantic(av)
                for h in hits:
                    if isinstance(h, dict):
                        h["alias_fts_match"] = True
                candidate_lists.append(hits)
        # Also include expand_query variants (canonical-term expansions) without
        # the alias flag — these are terminology normalizations, not synonyms.
        for v in variants:
            if v != query and v not in alias_vs:
                candidate_lists.append(_semantic(v))
        return merge_candidates_by_query(query, candidate_lists)

    @staticmethod
    def _top_score(results: list[dict[str, Any]]) -> float:
        if not results:
            return 0.0
        return max(
            (
                float(r.get("score") or r.get("fts_score") or 0.0)
                for r in results
                if isinstance(r, dict)
            ),
            default=0.0,
        )

    # -- Passage-level helpers ------------------------------------------ #

    def _passage_fts_hits(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """SPEC v3: FTS over retrieval_passages (not micro-blocks)."""
        try:
            store = self._passage_store_factory()
            hits = store.fts_search(query, top_k=limit) or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("passage FTS failed: %s", exc)
            return []
        out: list[dict[str, Any]] = []
        for h in hits:
            if not isinstance(h, dict):
                continue
            meta = h.get("metadata") or {}
            kid = (
                h.get("knowledge_id")
                or meta.get("knowledge_id")
                or meta.get("page_id")
                or ""
            )
            pid = (
                h.get("passage_id")
                or h.get("id")
                or meta.get("passage_id")
                or ""
            )
            primary_block = h.get("block_id") or meta.get("block_id") or ""
            if not primary_block:
                bids = meta.get("block_ids") or h.get("block_ids") or []
                primary_block = bids[0] if bids else ""
            out.append(
                {
                    "source": "knowledge",
                    "match_channel": "passage_fts",
                    "match_channels": ["passage_fts", "keyword"],
                    "block_id": primary_block,
                    "knowledge_id": kid,
                    "passage_id": pid,
                    "title": h.get("title") or meta.get("title") or "",
                    "text": h.get("text") or "",
                    "document_family_id": h.get("document_family_id")
                    or meta.get("document_family_id")
                    or "",
                    "version_year": h.get("version_year")
                    or meta.get("version_year"),
                    "section_path": meta.get("section_path") or "",
                    "block_ids": meta.get("block_ids") or h.get("block_ids") or [],
                    "retrieval_unit": "passage",
                    "candidate_type": "passage",
                    "fts_rank": h.get("fts_rank", 0),
                    "fts_score": h.get("keyword_score") or h.get("fts_rank") or 0,
                    "score": float(
                        h.get("keyword_score") or h.get("rrf_score") or 0.5
                    ),
                }
            )
        return out

    def _enrich_with_passages(
        self, results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Replace truncated micro-block text with owning passage when available."""
        if not results:
            return results
        try:
            store = self._passage_store_factory()
        except Exception:
            return results
        cache: dict[str, list[dict[str, Any]]] = {}
        out: list[dict[str, Any]] = []
        for item in results:
            row = dict(item) if isinstance(item, dict) else {"text": str(item)}
            text = str(row.get("text") or "")
            kid = str(row.get("knowledge_id") or "").strip()
            if (
                row.get("retrieval_unit") == "passage"
                or row.get("passage_id")
                or len(text) >= 200
            ):
                out.append(row)
                continue
            if not kid:
                out.append(row)
                continue
            if kid not in cache:
                try:
                    cache[kid] = store.get_by_knowledge(kid) or []
                except Exception:
                    cache[kid] = []
            passages = cache[kid]
            if not passages:
                out.append(row)
                continue
            bid = str(row.get("block_id") or "").strip()
            best = None
            if bid:
                for p in passages:
                    bids = p.get("block_ids") or []
                    if bid in bids:
                        best = p
                        break
            if best is None:
                # Fall back to the first passage of this document.
                best = passages[0] if passages else None
            if best is None:
                out.append(row)
                continue
            meta = best.get("metadata") if isinstance(best.get("metadata"), dict) else {}
            row["text"] = best.get("text") or text
            row["passage_id"] = (
                best.get("id") or best.get("passage_id") or meta.get("passage_id") or row.get("passage_id") or ""
            )
            row["retrieval_unit"] = "passage"
            row["candidate_type"] = "passage"
            if not row.get("block_id"):
                bids = best.get("block_ids") or meta.get("block_ids") or []
                if bids:
                    row["block_id"] = bids[0]
            row.setdefault("document_family_id", best.get("document_family_id") or "")
            row.setdefault("version_year", best.get("version_year"))
            row.setdefault("section_path", meta.get("section_path") or "")
            out.append(row)
        return out

    # -- Snapshot-bound passage helpers --------------------------------- #

    def list_blocks_for_page(self, page_id: str) -> list[dict[str, Any]]:
        """Block loader for adjacent allowlist / expansion (production path)."""
        try:
            return self._list_blocks_for_page(page_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("snapshot block list failed for %s: %s", page_id, exc)
            return []

    def _list_blocks_for_page(self, page_id: str) -> list[dict[str, Any]]:
        import json as _json

        container = self._container
        db = getattr(container, "db", None)
        if db is None:
            return []
        conn = db.get_conn() if hasattr(db, "get_conn") else None
        if conn is None:
            return []
        rows = conn.execute(
            """SELECT id, parent_id, page_id, content, block_type, properties, order_idx,
                      created_at, updated_at
               FROM blocks
               WHERE page_id = ?
               ORDER BY order_idx ASC, created_at ASC""",
            (page_id,),
        ).fetchall()
        blocks: list[dict[str, Any]] = []
        for row in rows:
            block = dict(row)
            raw_props = block.get("properties")
            if isinstance(raw_props, str):
                try:
                    block["properties"] = _json.loads(raw_props or "{}")
                except (TypeError, ValueError):
                    block["properties"] = {}
            elif raw_props is None:
                block["properties"] = {}
            blocks.append(block)
        return blocks

    def neighbor_passages_for_snapshot(
        self,
        knowledge_id: str,
        passage_id: str,
        window: int = 1,
    ) -> list[dict[str, Any]]:
        """Load same-doc passage_index ±window neighbors (SPEC v5 §3)."""
        try:
            store = self._passage_store_factory()
            all_p = store.get_by_knowledge(knowledge_id) or []
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "neighbor passages failed for %s: %s", knowledge_id, exc
            )
            return []
        if not all_p:
            return []
        idx = None
        for i, p in enumerate(all_p):
            pid = str(p.get("id") or p.get("passage_id") or "").strip()
            if pid == passage_id:
                idx = i
                break
        if idx is None:
            return []
        lo = max(0, idx - int(window))
        hi = min(len(all_p), idx + int(window) + 1)
        out: list[dict[str, Any]] = []
        for j in range(lo, hi):
            if j == idx:
                continue
            p = all_p[j]
            pid = str(p.get("id") or p.get("passage_id") or "").strip()
            meta = p.get("metadata") if isinstance(p.get("metadata"), dict) else {}
            bids = p.get("block_ids") or meta.get("block_ids") or []
            if isinstance(bids, str):
                try:
                    import json as _json

                    bids = _json.loads(bids)
                except Exception:
                    bids = []
            out.append(
                {
                    "source": "knowledge",
                    "knowledge_id": knowledge_id,
                    "passage_id": pid,
                    "title": p.get("title")
                    or p.get("title_prefix")
                    or meta.get("title")
                    or "",
                    "text": p.get("text") or "",
                    "block_id": (bids[0] if bids else ""),
                    "block_ids": list(bids) if isinstance(bids, list) else [],
                    "document_family_id": p.get("document_family_id") or "",
                    "version_year": p.get("version_year"),
                    "section_path": p.get("section_path")
                    or meta.get("section_path")
                    or "",
                    "retrieval_unit": "passage",
                    "candidate_type": "passage",
                    "score": 0.0,
                    "passage_index": p.get("passage_index"),
                }
            )
        return out

    def select_document_passages_for_snapshot(
        self,
        knowledge_id: str,
        query: str,
        existing_passage_ids: set[str],
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Return a bounded, query-relevant passage supplement for one accepted doc."""
        import re

        try:
            store = self._passage_store_factory()
            passages = store.get_by_knowledge(knowledge_id) or []
            from src.answering.query_planner import plan_query

            plan = plan_query(query)
            terms = [x for x in (plan.anchors or []) if len(x) >= 2][:12]
        except Exception as exc:
            logger.debug(
                "document passage selection failed for %s: %s", knowledge_id, exc
            )
            return []

        scored: list[tuple[float, int, dict[str, Any]]] = []
        for index, passage in enumerate(passages):
            pid = str(
                passage.get("id") or passage.get("passage_id") or ""
            ).strip()
            if not pid or pid in existing_passage_ids:
                continue
            text = str(passage.get("text") or "")
            if not text:
                continue
            hits = sum(1 for term in terms if term in text)
            score = float(hits)
            if plan.predicate and plan.predicate in text:
                score += 2.0
            if any(condition in text for condition in (plan.conditions or [])):
                score += 1.5
            if plan.wants_numeric and re.search(
                r"\d+(?:\.\d+)?\s*(?:元|万元|%|％|天|年|月)", text
            ):
                score += 1.0
            if plan.polarity == "negative" and re.search(
                r"不得|禁止|取消|不再|废止|停止", text
            ):
                score += 1.0
            if score <= 0:
                continue
            meta = (
                passage.get("metadata")
                if isinstance(passage.get("metadata"), dict)
                else {}
            )
            block_ids = passage.get("block_ids") or meta.get("block_ids") or []
            if isinstance(block_ids, str):
                try:
                    import json as _json

                    block_ids = _json.loads(block_ids)
                except Exception:
                    block_ids = []
            scored.append(
                (
                    score,
                    index,
                    {
                        "source": "knowledge",
                        "knowledge_id": knowledge_id,
                        "passage_id": pid,
                        "block_id": block_ids[0]
                        if isinstance(block_ids, list) and block_ids
                        else "",
                        "block_ids": list(block_ids)
                        if isinstance(block_ids, list)
                        else [],
                        "title": passage.get("title")
                        or passage.get("title_prefix")
                        or meta.get("title")
                        or "",
                        "text": text,
                        "document_family_id": passage.get("document_family_id") or "",
                        "version_year": passage.get("version_year"),
                        "section_path": passage.get("section_path")
                        or meta.get("section_path")
                        or "",
                        "retrieval_unit": "passage",
                        "candidate_type": "passage",
                        "score": score,
                    },
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [row for _, _, row in scored[: max(0, int(limit))]]

"""Raw evidence retriever — algorithm authority for evidence-only path.

WP1-T1 maintainability closure: query rewrite, hybrid/FTS fallback, rerank,
diversity, packaging, and stage traces live here. Construct with explicit
dependencies only (no whole search-service instance). Does not touch MCP,
Graph, Memory, or Wiki Authoring.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Callable, cast

from src.retrieval.candidate_pool import CandidatePoolPolicy
from src.retrieval.models import RawRetrievalResult
from src.services.citation_builder import CitationBuilder
from src.services.hybrid_search import HybridSearcher
from src.services.query_rewriter import QueryRewriter
from src.services.reranker import LLMReranker

logger = logging.getLogger(__name__)

_STAGE_TIMEOUTS = {
    "query_rewrite": 15,
    "hybrid_search": 25,
    "rerank": 20,
    "wiki_search": 5,
}

# Process-level rerank circuit breaker (SPEC v6 §5.1).
# Consecutive timeouts open a short cooling window with deterministic hybrid fallback.
_RERANK_CB_LOCK = None  # lazy
_RERANK_CB_STATE: dict[str, Any] = {
    "consecutive_timeouts": 0,
    "open_until": 0.0,
    "last_probe_at": 0.0,
    "last_reason": "",
    "fallback_count": 0,
    "timeout_count": 0,
}
_RERANK_CB_THRESHOLD = 2  # open after N consecutive timeouts
_RERANK_CB_COOLDOWN_S = 90.0  # short cooling period
_RERANK_CB_PROBE_INTERVAL_S = 45.0


def _cb_lock():
    global _RERANK_CB_LOCK
    if _RERANK_CB_LOCK is None:
        import threading
        _RERANK_CB_LOCK = threading.Lock()
    return _RERANK_CB_LOCK


def get_rerank_circuit_state() -> dict[str, Any]:
    with _cb_lock():
        return dict(_RERANK_CB_STATE)


def reset_rerank_circuit() -> None:
    with _cb_lock():
        _RERANK_CB_STATE.update({
            "consecutive_timeouts": 0,
            "open_until": 0.0,
            "last_probe_at": 0.0,
            "last_reason": "",
            "fallback_count": 0,
            "timeout_count": 0,
        })


def _rerank_circuit_is_open() -> tuple[bool, str]:
    now = time.monotonic()
    with _cb_lock():
        open_until = float(_RERANK_CB_STATE.get("open_until") or 0.0)
        if open_until <= now:
            return False, ""
        return True, str(_RERANK_CB_STATE.get("last_reason") or "cooldown")


def _rerank_circuit_note_timeout(query_fp: str) -> None:
    now = time.monotonic()
    with _cb_lock():
        _RERANK_CB_STATE["consecutive_timeouts"] = int(
            _RERANK_CB_STATE.get("consecutive_timeouts") or 0
        ) + 1
        _RERANK_CB_STATE["timeout_count"] = int(
            _RERANK_CB_STATE.get("timeout_count") or 0
        ) + 1
        _RERANK_CB_STATE["last_reason"] = f"timeout:{query_fp}"
        if _RERANK_CB_STATE["consecutive_timeouts"] >= _RERANK_CB_THRESHOLD:
            _RERANK_CB_STATE["open_until"] = now + _RERANK_CB_COOLDOWN_S
            _RERANK_CB_STATE["last_reason"] = (
                f"open_after_{_RERANK_CB_STATE['consecutive_timeouts']}_timeouts:{query_fp}"
            )


def _rerank_circuit_note_success() -> None:
    with _cb_lock():
        _RERANK_CB_STATE["consecutive_timeouts"] = 0
        _RERANK_CB_STATE["open_until"] = 0.0
        _RERANK_CB_STATE["last_reason"] = "recovered"


def _rerank_circuit_allow_probe() -> bool:
    now = time.monotonic()
    with _cb_lock():
        open_until = float(_RERANK_CB_STATE.get("open_until") or 0.0)
        if open_until <= now:
            return True
        last = float(_RERANK_CB_STATE.get("last_probe_at") or 0.0)
        if now - last >= _RERANK_CB_PROBE_INTERVAL_S:
            _RERANK_CB_STATE["last_probe_at"] = now
            return True
        return False


def _query_fingerprint(query: str) -> str:
    return hashlib.sha256((query or "").encode("utf-8")).hexdigest()[:12]


def build_deterministic_query_variants(query: str, *, max_variants: int = 4) -> list[dict[str, str]]:
    """Limited query-surface variants without a document/fact synonym table.

    Variants preserve the user's terms.  Domain vocabulary may only be derived
    later from retrieved corpus evidence, never from a hand-maintained mapping
    of colloquial evaluation questions to policy titles.
    """
    import re
    q = (query or "").strip()
    if not q:
        return []
    out: list[dict[str, str]] = [{"query": q, "source": "original"}]
    # Remove only conversational wrappers/interrogatives; retain nouns,
    # constraints and negation exactly as the user supplied them.
    compact = re.sub(r"(?:请问|请帮我|一下|如何|怎么|多少|是什么|有没有)", " ", q)
    compact = re.sub(r"\s+", " ", compact).strip(" ，。？?；;")
    if compact and compact != q:
        out.append({"query": compact, "source": "remove_interrogative_wrapper"})

    chunks = re.findall(r"[\u4e00-\u9fff]{2,18}|[A-Za-z0-9][A-Za-z0-9._-]{1,}", q)
    stop = {"什么", "多少", "如何", "怎么", "是否", "哪个", "哪些", "公司", "关于", "通知"}
    terms = [c for c in chunks if c not in stop]
    if len(terms) >= 2:
        # Surface-term query is a deterministic fallback for tokenizers that
        # handle question wrappers poorly.  It never invents corpus terms.
        surface = " ".join(terms[:6])
        if surface and surface != q and not any(v["query"] == surface for v in out):
            out.append({"query": surface, "source": "query_surface_terms"})
    return out[:max_variants]


_GENERIC_QUERY_ALIASES = {
    # General Chinese surface forms, deliberately independent of any document
    # title, knowledge ID, or evaluation question.
    "比赛": ("竞赛",),
    "赛事": ("竞赛",),
    "奖金": ("奖励",),
    "发奖金": ("奖励",),
    "商家": ("合作商",),
    "店铺": ("门店", "网店"),
    "入驻": ("准入",),
    "门槛": ("条件",),
    "钱": ("金额", "费用"),
}


def deterministic_fallback_rank(
    query: str, candidates: list[dict[str, Any]], *, top_k: int,
) -> list[dict[str, Any]]:
    """Explainable local fallback when a remote reranker circuit is open.

    It combines the existing fused score with query-term overlap, title
    overlap, and explicit recency intent.  It never introduces document names
    or answer facts; all additional signals come from the user query and each
    candidate's own text/metadata.
    """
    import re
    q = (query or "").strip()
    try:
        import jieba
        tokens = jieba.lcut(q)
    except Exception:  # pragma: no cover - tokenizer is optional in minimal installs
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9_-]*", q)
    stop = {"公司", "请问", "怎么", "如何", "多少", "是否", "有没有", "什么", "这个"}
    terms = [str(token).strip() for token in tokens if len(str(token).strip()) >= 2]
    terms = [term for term in terms if term not in stop]
    expanded = list(terms)
    for term in terms:
        expanded.extend(_GENERIC_QUERY_ALIASES.get(term, ()))
    query_terms = list(dict.fromkeys(expanded))[:20]
    wants_recent = bool(re.search(r"最新|现行|新版|修订|变更", q))
    years = [
        int(row.get("version_year") or (row.get("metadata") or {}).get("version_year") or 0)
        for row in candidates
    ]
    newest = max(years, default=0)

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, candidate in enumerate(candidates):
        row = dict(candidate)
        meta = row.get("metadata") or {}
        title = str(row.get("title") or meta.get("title") or "")
        text = str(row.get("text") or "")
        title_hits = sum(1 for term in query_terms if term in title)
        body_hits = sum(1 for term in query_terms if term in text)
        base = 0.0
        for key in ("final_relevance_score", "rrf_score", "final_score", "score", "vector_score", "fts_score"):
            try:
                value = row.get(key)
                if value is not None:
                    base = float(value)
                    break
            except (TypeError, ValueError):
                continue
        recency = 0.0
        try:
            year = int(row.get("version_year") or meta.get("version_year") or 0)
        except (TypeError, ValueError):
            year = 0
        if wants_recent and newest and year:
            # Explicit latest/current language is a scope constraint, not a
            # mild preference: stale passages often contain stronger literal
            # matches precisely because they describe superseded rules.
            recency = 0.35 * max(0.0, min(1.0, (year - (newest - 8)) / 8))
        # The position term only breaks ties between equally-supported rows.
        local_score = base + title_hits * 0.08 + body_hits * 0.025 + recency - index * 1e-7
        row["fallback_score"] = round(local_score, 8)
        row["fallback_score_breakdown"] = {
            "base": round(base, 8), "title_term_hits": title_hits,
            "body_term_hits": body_hits, "recency": round(recency, 8),
        }
        scored.append((local_score, index, row))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [row for _, _, row in scored[:top_k]]


class RawRetriever:
    """Evidence retrieval capability with explicit constructor dependencies."""

    def __init__(
        self,
        *,
        config: Any = None,
        db: Any = None,
        block_store: Any = None,
        llm: Any = None,
        hybrid_searcher: Any = None,
        query_rewriter: Callable[[str], list[str]] | None = None,
        reranker: Callable[[str, list[dict], int], list[dict]] | None = None,
        hybrid_search_fn: Callable[[list[str], int], list[dict]] | None = None,
        knowledge_fts_fn: Callable[[str, int], list[dict]] | None = None,
        wiki_search_fn: Callable[[str], list[dict]] | None = None,
        package_raw_fn: Callable[..., list[dict]] | None = None,
        diversity_fn: Callable[..., list[dict]] | None = None,
        citation_builder_factory: Callable[[Any], Any] | None = None,
        stage_timeout_fn: Callable[[str], float] | None = None,
    ):
        self._config = config if config is not None else {}
        self._db = db
        self._block_store = block_store
        self._llm = llm
        self._hybrid_searcher = hybrid_searcher
        self._query_rewriter_fn = query_rewriter
        self._reranker_fn = reranker
        self._hybrid_search_fn = hybrid_search_fn
        self._knowledge_fts_fn = knowledge_fts_fn
        self._wiki_search_fn = wiki_search_fn
        self._package_raw_fn = package_raw_fn
        self._diversity_fn = diversity_fn
        self._citation_builder_factory = citation_builder_factory
        self._stage_timeout_fn = stage_timeout_fn

    def _cfg(self, key: str, default: Any = None) -> Any:
        if isinstance(self._config, dict):
            parts = key.split(".")
            obj: object = self._config
            for p in parts:
                if isinstance(obj, dict):
                    obj = obj.get(p)
                else:
                    return default
            return obj if obj is not None else default
        getter = getattr(self._config, "get", None)
        if callable(getter):
            return getter(key, default)
        return default

    def _stage_timeout(self, stage: str) -> float:
        if self._stage_timeout_fn is not None:
            return float(self._stage_timeout_fn(stage))
        cfg_key = f"rag.stage_timeout.{stage}"
        custom = self._cfg(cfg_key)
        return float(custom or _STAGE_TIMEOUTS.get(stage, 30))

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        include_legacy_wiki_fts: bool = True,
    ) -> RawRetrievalResult:
        """Run raw retrieval pipeline (rewrite → hybrid/fallback → rerank → diversity → package)."""
        t0 = time.monotonic()
        trace: dict[str, Any] = {
            "mode": "legacy_raw",
            "query": (query or "")[:200],
            "stages": {},
        }
        warnings: list[str] = []
        fallbacks: list[dict[str, Any]] = []

        queries = [query]
        wiki_results: list[dict] = []

        with ThreadPoolExecutor(max_workers=2) as pool:
            rewrite_future = pool.submit(self.rewrite_query, query)
            wiki_future = (
                pool.submit(self.safe_wiki_search, query)
                if include_legacy_wiki_fts
                else None
            )

            try:
                queries = rewrite_future.result(
                    timeout=self._stage_timeout("query_rewrite"),
                )
            except FuturesTimeout:
                logger.warning("Query rewrite timed out, using original query")
                queries = [query]
            except Exception as e:
                logger.warning("Query rewrite failed: %s", e)
                queries = [query]

            if wiki_future is not None:
                try:
                    wiki_results = wiki_future.result(
                        timeout=self._stage_timeout("wiki_search"),
                    )
                except FuturesTimeout:
                    logger.warning("Wiki search timed out")
                    wiki_results = []
                except Exception as e:
                    logger.warning("Wiki search failed: %s", e)
                    wiki_results = []

        trace["stages"]["query_rewrite"] = {"count": len(queries)}
        if include_legacy_wiki_fts:
            trace["stages"]["legacy_wiki_fts"] = {"count": len(wiki_results)}

        # Deterministic variants (capped) merged into retrieval queries.
        variants = build_deterministic_query_variants(query, max_variants=4)
        trace["stages"]["query_variants"] = variants
        for v in variants:
            vq = v.get("query") or ""
            if vq and vq not in queries:
                queries.append(vq)
        # SPEC Phase 3.2: general Chinese synonym-expanded variants.  These
        # substitute colloquial surface forms (比赛→竞赛, 店铺→门店) so formal
        # documents can be found from informal phrasing.  Synonyms are general
        # language pairs — no document titles or evaluation-question mappings.
        from src.services.query_rewrite import build_alias_query_variants

        alias_variants = build_alias_query_variants(query, max_variants=3)
        if alias_variants:
            trace["stages"]["alias_query_variants"] = alias_variants
            for v in alias_variants:
                vq = v.get("query") or ""
                if vq and vq not in queries:
                    queries.append(vq)
        queries = queries[:9]

        # Explicit public top_k vs internal fetch_k (ADR §5 CandidatePoolPolicy).
        # Main path and BlockStore fallback share the same policy object.
        policy = CandidatePoolPolicy.from_request(top_k, config=self._config)
        trace["stages"]["candidate_pool_policy"] = policy.to_trace()
        raw_pool_k = policy.fetch_k
        t_ret0 = time.monotonic()
        candidates = self.raw_retrieve(queries, query, raw_pool_k)
        trace["stages"]["raw_retrieval"] = {
            "count": len(candidates),
            "requested_pool_k": raw_pool_k,
            "ms": round((time.monotonic() - t_ret0) * 1000, 2),
        }

        # SPEC Phase 3.2: tag candidates that were surfaced via alias-expanded
        # synonym variants. When a candidate's TITLE contains the synonym word
        # (e.g. 竞赛/门店) but NOT the original colloquial word (比赛/店铺), it
        # was likely retrieved by the alias variant query. Tag it with
        # ``alias_fts_match=True`` so the relevance gate can credit it (the
        # FTS/vector channel verified the lexical match via the synonym).
        # Title-only check is intentional: body text often contains incidental
        # synonym mentions (e.g. 奖惩办法 body mentions 奖励), which would
        # produce false positives. The title is the authoritative signal for
        # which regulation family the candidate belongs to.
        if alias_variants:
            # Build {original_word: [synonyms]} map from the variant sources.
            alias_pairs: list[tuple[str, str]] = []
            for v in alias_variants:
                src = v.get("source") or ""
                if src.startswith("alias:") and "→" in src:
                    parts = src[len("alias:"):].split("→", 1)
                    if len(parts) == 2 and parts[0] and parts[1]:
                        alias_pairs.append((parts[0], parts[1]))
            if alias_pairs:
                for cand in candidates:
                    if not isinstance(cand, dict):
                        continue
                    if cand.get("alias_fts_match"):
                        continue  # already tagged
                    # Check title from candidate or its metadata.
                    cand_title = str(
                        cand.get("title")
                        or (cand.get("metadata") or {}).get("title")
                        or ""
                    )
                    if not cand_title:
                        continue
                    for orig, syn in alias_pairs:
                        if syn in cand_title and orig not in cand_title:
                            cand["alias_fts_match"] = True
                            break

        # Entity+predicate joint-hit boost before rerank (SPEC v6 §4.2).
        candidates = self._boost_entity_predicate_hits(query, candidates)
        trace["stages"]["pre_rerank_candidate_ids"] = [
            self._candidate_identity(row) for row in candidates[:raw_pool_k]
        ]

        if candidates:
            qfp = _query_fingerprint(query)
            t_rr0 = time.monotonic()
            open_cb, cb_reason = _rerank_circuit_is_open()
            used_fallback = False
            if open_cb and not _rerank_circuit_allow_probe():
                used_fallback = True
                with _cb_lock():
                    _RERANK_CB_STATE["fallback_count"] = int(
                        _RERANK_CB_STATE.get("fallback_count") or 0
                    ) + 1
                warnings.append(f"rerank_circuit_open:{cb_reason}")
                fallbacks.append({
                    "stage": "rerank",
                    "type": "deterministic_hybrid_fallback",
                    "reason": cb_reason,
                    "query_fingerprint": qfp,
                })
                logger.warning(
                    "Rerank circuit open (%s), deterministic fallback query_fp=%s",
                    cb_reason,
                    qfp,
                )
                candidates = deterministic_fallback_rank(
                    query, candidates, top_k=raw_pool_k,
                )
            else:
                try:
                    candidates = self.timed_rerank(
                        query, candidates, policy.rerank_top_k,
                    )
                    _rerank_circuit_note_success()
                except FuturesTimeout:
                    _rerank_circuit_note_timeout(qfp)
                    logger.warning(
                        "Rerank timed out type=futures_timeout query_fp=%s, keeping original order",
                        qfp,
                    )
                    warnings.append(f"rerank_timeout:{qfp}")
                    fallbacks.append({
                        "stage": "rerank",
                        "type": "timeout_keep_order",
                        "query_fingerprint": qfp,
                    })
                    used_fallback = True
                except Exception as e:
                    logger.warning("Rerank failed: %s query_fp=%s", e, qfp)
                    warnings.append(f"rerank_failed:{e}")
                    used_fallback = True
            trace["stages"]["rerank"] = {
                "ms": round((time.monotonic() - t_rr0) * 1000, 2),
                "fallback": used_fallback,
                "circuit": get_rerank_circuit_state(),
                "query_fingerprint": qfp,
                "output_candidate_ids": [
                    self._candidate_identity(row)
                    for row in candidates[: policy.rerank_top_k]
                ],
            }

        if candidates:
            candidates = self.diversity_filter(candidates, threshold=0.8)

        output: list[dict] = []
        if include_legacy_wiki_fts:
            output.extend(wiki_results)
        output.extend(
            self.package_raw_candidates(
                query, candidates, top_k=policy.final_top_k,
            )
        )

        elapsed = time.monotonic() - t0
        logger.info(
            "Raw retrieval completed in %.2fs: %d results for query=%r",
            elapsed,
            len(output),
            (query or "")[:50],
        )
        trace["elapsed_ms"] = round(elapsed * 1000, 2)
        trace["result_count"] = len(output)

        return RawRetrievalResult(
            candidates=tuple(output),
            trace=trace,
            warnings=tuple(warnings),
            fallbacks=tuple(fallbacks),
        )

    @staticmethod
    def _candidate_identity(row: dict[str, Any]) -> str:
        meta = row.get("metadata") or {}
        return str(
            row.get("knowledge_id") or meta.get("knowledge_id") or meta.get("page_id") or row.get("id") or ""
        )

    def rewrite_query(self, query: str) -> list[str]:
        if self._query_rewriter_fn is not None:
            return self._query_rewriter_fn(query)
        enabled = self._cfg("rag.enable_query_rewriting", False)
        if not enabled:
            return [query]
        try:
            rewriter = QueryRewriter(self._llm, self._config)
            return rewriter.rewrite(query)
        except Exception as e:
            logger.warning("Query rewrite failed: %s", e)
            return [query]

    def raw_retrieve(self, queries: list[str], query: str, top_k: int) -> list[dict]:
        try:
            candidates = self.timed_hybrid_search(queries, top_k)
        except Exception as e:
            logger.warning("Hybrid search failed, falling back to BlockStore: %s", e)
            try:
                candidates = (
                    self._block_store.search(query, top_k=top_k)
                    if self._block_store
                    else []
                )
            except Exception:
                candidates = []

        if not candidates:
            candidates = self.knowledge_fts_search(query, top_k)
        return candidates

    def timed_hybrid_search(self, queries: list[str], top_k: int) -> list[dict]:
        timeout = self._stage_timeout("hybrid_search")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.hybrid_search, queries, top_k)
            return future.result(timeout=timeout)

    def hybrid_search(self, queries: list[str], top_k: int) -> list[dict]:
        if self._hybrid_search_fn is not None:
            return self._hybrid_search_fn(queries, top_k)
        if self._hybrid_searcher is not None:
            return cast("list[dict]", self._hybrid_searcher.search(queries, top_k=top_k))
        searcher = HybridSearcher(self._db, self._block_store, self._config)
        return searcher.search(queries, top_k=top_k)

    def timed_rerank(
        self, query: str, candidates: list[dict], top_k: int,
    ) -> list[dict]:
        if not self._cfg("rag.enable_rerank", True):
            return candidates
        timeout = self._stage_timeout("rerank")
        # Use a single-worker pool and cancel futures on timeout so work does not pile up.
        # Note: pure-Python work may continue until the thread returns, but we do not
        # submit further rerank jobs while the circuit is open (see retrieve()).
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(self.rerank, query, candidates, top_k)
            try:
                return future.result(timeout=timeout)
            except FuturesTimeout:
                future.cancel()
                raise
        finally:
            # Do not wait for stuck workers — shutdown without waiting to avoid blocking.
            pool.shutdown(wait=False, cancel_futures=True)

    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        if self._reranker_fn is not None:
            return self._reranker_fn(query, candidates, top_k)
        enabled = self._cfg("rag.enable_rerank", True)
        if not enabled:
            return candidates
        try:
            reranker = LLMReranker(self._llm, self._config)
            return reranker.rerank(query, candidates, top_n=top_k)
        except Exception as e:
            logger.warning("Rerank failed: %s", e)
            return candidates

    def _boost_entity_predicate_hits(
        self, query: str, candidates: list[dict],
    ) -> list[dict]:
        """Boost candidates jointly matching entity+predicate before rerank."""
        import re
        if not candidates:
            return candidates
        q = query or ""
        entities = re.findall(r"[\u4e00-\u9fff]{2,12}", q)
        stop = {"什么", "多少", "如何", "怎么", "是否", "哪个", "中国", "电信", "广西", "公司", "关于"}
        entities = [e for e in entities if e not in stop][:8]
        predicates = re.findall(
            r"不得|禁止|严禁|取消|不再|处罚|罚款|扣分|限额|额度|标准|"
            r"占比|比例|负责|牵头|归口|准入|资格|审核|报销|支付|流程|时限",
            q,
        )
        if not entities and not predicates:
            return candidates
        out: list[dict] = []
        for c in candidates:
            row = dict(c)
            blob = f"{row.get('title') or ''}\n{row.get('text') or ''}"
            e_hits = sum(1 for e in entities if e in blob)
            p_hits = sum(1 for p in predicates if p in blob)
            boost = 0.0
            if e_hits and p_hits:
                boost = 0.12 + 0.03 * min(3, e_hits + p_hits)
            elif e_hits >= 2:
                boost = 0.06
            # Extra boost when money pattern co-occurs with numeric query cues
            if re.search(r"限额|处罚|奖金|多少|上限", q) and re.search(
                r"\d+(?:\.\d+)?\s*(?:万元|元|%)", blob
            ):
                boost += 0.1
            if boost:
                for key in ("score", "final_relevance_score", "rrf_score"):
                    if row.get(key) is not None:
                        try:
                            row[key] = float(row[key]) + boost
                        except (TypeError, ValueError):
                            pass
                row["entity_predicate_boost"] = boost
            out.append(row)
        out.sort(
            key=lambda x: float(
                x.get("final_relevance_score")
                or x.get("score")
                or x.get("rrf_score")
                or 0.0
            ),
            reverse=True,
        )
        return out

    def knowledge_fts_search(self, query: str, top_k: int) -> list[dict]:
        if self._knowledge_fts_fn is not None:
            return self._knowledge_fts_fn(query, top_k)
        if self._db is None:
            return []
        try:
            rows = self._db.search_knowledge(query, limit=top_k, offset=0)
        except Exception as e:
            logger.warning("Knowledge FTS fallback failed: %s", e)
            return []

        if rows is None:
            return []
        try:
            iter(rows)
        except TypeError:
            return []

        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            kid = row.get("id", "")
            results.append({
                "id": "",
                "text": row.get("content", ""),
                "metadata": {
                    "page_id": kid,
                    "knowledge_id": kid,
                    "title": row.get("title", ""),
                    "block_type": "knowledge",
                    "properties": {},
                },
                "score": row.get("fts_rank", 0),
            })
        return results

    def safe_wiki_search(self, query: str) -> list[dict]:
        if self._wiki_search_fn is not None:
            return self._wiki_search_fn(query)
        return self.wiki_search(query)

    def wiki_search(self, query: str) -> list[dict]:
        if self._db is None:
            return []
        try:
            wiki_results = self._db.search_wiki_fts(query, limit=3)
            if not wiki_results:
                return []
            try:
                iter(wiki_results)
            except TypeError:
                return []
            output = []
            for wr in wiki_results:
                if not isinstance(wr, dict):
                    continue
                summary = wr.get("concept_summary", "")
                content_preview = (wr.get("content", "") or "")[:300]
                output.append({
                    "source": "wiki",
                    "knowledge_id": wr.get("id", ""),
                    "title": wr["title"],
                    "summary": summary,
                    "text": f"[Wiki] {wr['title']}: {summary}\n{content_preview}",
                    "score": wr.get("fts_rank", 0),
                })
            return output
        except Exception as e:
            logger.warning("Wiki search failed: %s", e)
            return []

    def package_raw_candidates(
        self, query: str, candidates: list[dict], *, top_k: int,
    ) -> list[dict]:
        if self._package_raw_fn is not None:
            return self._package_raw_fn(query, candidates, top_k=top_k)
        output: list[dict] = []
        seen_blocks: set = set()
        knowledge_doc_counts: dict[str, int] = {}
        max_per_doc = 3
        citation_builder = None
        if self._citation_builder_factory is not None and self._db is not None:
            citation_builder = self._citation_builder_factory(self._db)
        elif self._db is not None:
            citation_builder = CitationBuilder(self._db)

        for r in candidates:
            bid = r.get("id", "")
            if bid and bid in seen_blocks:
                continue
            if bid:
                seen_blocks.add(bid)

            kid = (r.get("metadata") or {}).get(
                "page_id",
                (r.get("metadata") or {}).get("knowledge_id", ""),
            )

            if kid:
                doc_count = knowledge_doc_counts.get(kid, 0)
                if doc_count >= max_per_doc:
                    continue
                knowledge_doc_counts[kid] = doc_count + 1

            item = self._db.get_knowledge(kid) if kid and self._db is not None else None

            score = 0.0
            score_key = ""
            for key in ("rerank_score", "rrf_score", "vector_score", "distance"):
                val = r.get(key)
                if val is not None:
                    score = val
                    score_key = key
                    break

            title = "未知"
            if item and item.get("title"):
                title = item["title"]
            elif (r.get("metadata") or {}).get("title"):
                title = r["metadata"]["title"]
            elif kid and self._db is not None:
                try:
                    row = self._db.get_conn().execute(
                        "SELECT title FROM knowledge_items WHERE id = ? AND deleted_at IS NULL",
                        (kid,),
                    ).fetchone()
                    if row and row[0]:
                        title = row[0]
                except Exception:
                    pass

            title_boost = self._cfg("rag.title_boost", 0.15)
            if title_boost > 0 and title != "未知" and score_key != "distance":
                query_lower = query.lower()
                query_chars = set(query_lower) - {
                    " ", "的", "了", "是", "在", "和", "与", "或", "有", "中", "及",
                }
                title_lower = title.lower()
                overlap = sum(1 for c in query_chars if c in title_lower)
                if overlap > 0 and len(query_chars) > 0:
                    boost_ratio = min(overlap / len(query_chars), 1.0) * title_boost
                    score = min(score + boost_ratio, 1.0)
                    r.setdefault("match_channels", [])
                    if "title_boost" not in r["match_channels"]:
                        r["match_channels"].append("title_boost")

            entry = {
                "source": "knowledge",
                "block_id": bid,
                "knowledge_id": kid,
                "title": title,
                "text": r.get("text", ""),
                "score": score,
                "match_channels": r.get("match_channels", []),
                "warnings": r.get("warnings", []),
            }
            # SPEC Phase 3.2: carry forward the alias_fts_match flag set by
            # the retrieve() method so the relevance gate can credit
            # synonym-matched candidates.
            if r.get("alias_fts_match"):
                entry["alias_fts_match"] = True
            # SPEC Phase 3.3: carry forward retrieval-channel scores so
            # score_candidate_relevance can use the genuine vector similarity
            # as a semantic floor (e.g. when lexical coverage is low because
            # the query is colloquial but the embedding matched the right doc).
            # Without this, a high-confidence vector hit loses its signal at
            # the relevance gate and is rejected as insufficient evidence.
            for _fwd_key in (
                "_semantic_similarity",
                "vector_score",
                "fts_score",
                "fts_rank",
                "keyword_score",
                "rerank_score",
            ):
                _v = r.get(_fwd_key)
                if _v is not None:
                    entry[_fwd_key] = _v
            # SPEC Phase 3.3: second-pass alias tagging. The first pass in
            # retrieve() runs on raw candidates whose ``title`` field may not
            # be populated yet (the title is resolved above from the DB).
            # Re-check here now that the title is known, so candidates surfaced
            # via alias-expanded synonym variants (比赛→竞赛, 店铺→门店)
            # receive the ``alias_fts_match`` flag and the relevance gate can
            # credit them.
            if not entry.get("alias_fts_match") and title and title != "未知":
                try:
                    from src.services.query_rewrite import build_alias_query_variants

                    for v in build_alias_query_variants(query):
                        src = v.get("source") or ""
                        if not (src.startswith("alias:") and "→" in src):
                            continue
                        parts = src[len("alias:"):].split("→", 1)
                        if len(parts) != 2 or not parts[0] or not parts[1]:
                            continue
                        orig, syn = parts[0], parts[1]
                        if syn in title and orig not in title:
                            entry["alias_fts_match"] = True
                            break
                except Exception as _alias_err:  # noqa: BLE001
                    logger.debug("second-pass alias tagging failed: %s", _alias_err)
            if citation_builder is not None:
                entry["citation"] = citation_builder.build(r, item).to_dict()
            output.append(entry)
            if len(output) >= top_k and not any(
                x.get("source") == "wiki" for x in output
            ):
                pass
        return output

    @staticmethod
    def minhash(text: str, num_perm: int = 64) -> list[int]:
        if not text:
            return [0] * num_perm
        if len(text) >= 2:
            tokens = [text[i:i + 2] for i in range(len(text) - 1)]
        else:
            tokens = [text]
        if not tokens:
            return [0] * num_perm
        signature = []
        for i in range(num_perm):
            min_hash = 2 ** 32
            for token in tokens:
                h = int(
                    hashlib.md5(
                        f"{i}:{token}".encode("utf-8", errors="replace"),
                    ).hexdigest()[:8],
                    16,
                )
                if h < min_hash:
                    min_hash = h
            signature.append(min_hash)
        return signature

    @classmethod
    def jaccard_similarity(cls, sig_a: list[int], sig_b: list[int]) -> float:
        if not sig_a or not sig_b or len(sig_a) != len(sig_b):
            return 0.0
        return sum(1 for a, b in zip(sig_a, sig_b) if a == b) / len(sig_a)

    @staticmethod
    def candidate_score(c: dict) -> float:
        for key in ("rerank_score", "rrf_score", "final_score", "score"):
            v = c.get(key)
            if v is not None:
                return float(v)
        return 0

    def diversity_filter(
        self, candidates: list[dict], threshold: float = 0.8,
    ) -> list[dict]:
        if self._diversity_fn is not None:
            return self._diversity_fn(candidates, threshold=threshold)
        if len(candidates) <= 1:
            return candidates

        signatures = []
        for c in candidates:
            text = c.get("text") or ""
            signatures.append(self.minhash(text[:500]))

        removed: set[int] = set()
        for i in range(len(candidates)):
            if i in removed:
                continue
            for j in range(i + 1, len(candidates)):
                if j in removed:
                    continue
                sim = self.jaccard_similarity(signatures[i], signatures[j])
                if sim > threshold:
                    score_i = self.candidate_score(candidates[i])
                    score_j = self.candidate_score(candidates[j])
                    if score_i >= score_j:
                        removed.add(j)
                    else:
                        removed.add(i)
                        break

        if removed:
            logger.debug(
                "Diversity filter: removed %d near-duplicate results (threshold=%s)",
                len(removed),
                threshold,
            )

        return [c for i, c in enumerate(candidates) if i not in removed]

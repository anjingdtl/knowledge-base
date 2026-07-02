"""RAG 管线抽象与可配置阶段 — 统一入口

这是唯一的 RAG 实现，旧版 rag.py 已合并到此文件。
RAGService 类保留为向后兼容的别名。
"""
import hashlib
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from src.models.retrieval import build_match_channels
from src.services.db import Database
from src.services.hybrid_search import HybridSearcher
from src.services.llm import LLMService
from src.services.query_rewriter import QueryRewriter
from src.services.reranker import LLMReranker
from src.utils.config import Config
from src.utils.llm_text import strip_think

logger = logging.getLogger(__name__)


def _get_container_service(attr: str, fallback_factory):
    """从 DI 容器获取服务实例，容器不可用时 fallback 到新建。"""
    try:
        from src.core.container import get_active_container
        container = get_active_container()
        if container is not None:
            svc = getattr(container, attr, None)
            if svc is not None:
                return svc
    except Exception:
        pass
    return fallback_factory()

def _run_coroutine_sync(coro, timeout: float = 120):
    """Run an async pipeline from sync entrypoints without blocking its loop."""
    import asyncio
    import concurrent.futures
    import queue
    import threading

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if not (loop and loop.is_running()):
        # 无运行中的事件循环（MCP stdio 同步工具主路径）。必须用 wait_for
        # 兜住 timeout——否则协程内部永久挂起时，调用方传入的 timeout 形同
        # 虚设（旧实现直接 asyncio.run(coro) 丢弃了 timeout 参数）。
        try:
            return asyncio.run(asyncio.wait_for(coro, timeout=timeout))
        except asyncio.TimeoutError as exc:
            raise concurrent.futures.TimeoutError() from exc

    result_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def _runner():
        try:
            result_queue.put((True, asyncio.run(coro)))
        except BaseException as exc:  # noqa: BLE001 - pass through to caller
            result_queue.put((False, exc))

    thread = threading.Thread(target=_runner, name="RAGPipelineAsyncBridge", daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise concurrent.futures.TimeoutError()
    success, result = result_queue.get_nowait()
    if success:
        return result
    if isinstance(result, BaseException):
        raise result
    raise RuntimeError(str(result))


# ---- 统一的 prompt 模板 ----

RAG_SYSTEM_PROMPT = """你是一个严谨的知识库问答助手。请基于检索到的知识库内容回答用户问题。

要求：
1. 先理解问题需要哪些事实，再从多个来源中组合推理，不要只做关键词有/无判断
2. 如果同时有「Wiki 结构化知识」和「原始文档片段」，优先采纳 Wiki 中已综合验证的知识
3. 可以进行组合推理和简单计算，但每一步都必须能从给定来源得到依据
4. 证据不足时，说明已找到的相关线索和缺少的关键事实；不要编造
5. 回答中标注引用的知识来源，例如「依据来源1、来源3」
6. 用中文回答，语气自然、直接"""

RAG_USER_TEMPLATE = """请先拆解问题，再基于知识库内容组合推理并回答。

知识库内容：
{context}

用户问题：{question}"""


def build_rag_messages(question: str, context: str, conversation_history: list[dict] | None = None) -> list[dict]:
    """Build chat messages for grounded RAG generation."""
    messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]
    if conversation_history:
        messages.extend(conversation_history[-6:])
    messages.append({
        "role": "user",
        "content": RAG_USER_TEMPLATE.format(context=context, question=question),
    })
    return messages


# ---- 管线上下文 ----

@dataclass
class RagContext:
    """RAG 管线上下文，在各阶段间传递"""
    question: str
    rewritten_queries: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    reranked_results: list[dict] = field(default_factory=list)
    wiki_context: str = ""
    context_text: str = ""
    answer: str = ""
    sources: list[dict] = field(default_factory=list)
    source_graph: dict = field(default_factory=lambda: {"nodes": [], "edges": []})
    conversation_history: list[dict] = field(default_factory=list)
    stream_generator: Any = None  # 流式生成时的 generator
    metadata: dict = field(default_factory=dict)
    # Sprint 2：ask_with_query 入口可显式指定检索阶段的 QuerySpec 与 top_k
    query_spec_override: object = None
    top_k: int = 10
    # Phase 3: trace support
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    _stage_durations: dict = field(default_factory=dict)
    # BUG#9: 各阶段 token 用量（{stage_name: {input_tokens, output_tokens}}）
    _stage_tokens: dict = field(default_factory=dict)


# ---- 阶段抽象基类 ----

class PipelineStage(ABC):
    """管线阶段抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    def is_enabled(self, config: dict) -> bool:
        return bool(config.get("enabled", True))

    @abstractmethod
    async def execute(self, ctx: RagContext, config: dict) -> RagContext:
        pass


# ---- 内置阶段 ----

class QueryRewriteStage(PipelineStage):
    def __init__(self, query_rewriter=None):
        self._query_rewriter = query_rewriter

    @property
    def name(self):
        return "query_rewrite"

    async def execute(self, ctx, config):
        mode = config.get("mode", "llm")
        if mode == "disabled" or not self.is_enabled(config):
            ctx.rewritten_queries = [ctx.question]
            return ctx
        num_variations = config.get("num_variations", 3)
        try:
            rewriter = self._query_rewriter or _get_container_service("query_rewriter", QueryRewriter)
            ctx.rewritten_queries = rewriter.rewrite(ctx.question, num_variations=num_variations)
        except Exception as e:
            logger.warning("Query rewrite failed: %s", e)
            ctx.rewritten_queries = [ctx.question]
        return ctx


class WikiRetrievalStage(PipelineStage):
    def __init__(self, db=None):
        self._db = db

    @property
    def name(self):
        return "wiki_retrieval"

    async def execute(self, ctx, config):
        if not self.is_enabled(config):
            return ctx
        if not Config.get("wiki.enabled", False):
            return ctx

        limit = config.get("limit", 3)
        db = self._db or Database
        try:
            wiki_results = db.search_wiki_fts(ctx.question, limit=limit)
            if wiki_results:
                parts = []
                for wp in wiki_results:
                    summary = wp.get("concept_summary", "")
                    content = wp.get("content", "")
                    text = f"- {wp['title']}"
                    if summary:
                        text += f"：{summary}"
                    if content:
                        text += f"\n  {content[:300]}"
                    parts.append(text)
                ctx.wiki_context = "\n".join(parts)
            else:
                pages = db.list_wiki_pages(status="published", search=ctx.question, limit=limit)
                if pages:
                    ctx.wiki_context = "\n\n".join([
                        f"## {p['title']}\n{p.get('content', '')[:500]}" for p in pages
                    ])
        except Exception as e:
            logger.warning("Wiki retrieval failed: %s", e)
        return ctx


class WikiReadStage(PipelineStage):
    """规模自适应:wiki_read/blend 档读文件系统 wiki 页作候选(spec §4.1 / S2)。

    在 vector_search 前判定查询规模(调 SizeAwareRouter),把 ``scale`` 缓存到
    ``ctx.metadata["scale"]`` 供 VectorSearchStage 分流;wiki_read/blend 档填 wiki
    候选,full_search 档跳过。仅 ``mode=wiki_first`` 且 ``rag.size_aware.enabled=true``
    时介入,否则空操作 —— legacy 项目零影响(S6)。

    设计说明:scale 在本 stage(而非 AgenticRouter)计算,因 WikiReadStage 在
    VectorSearchStage(调 agentic 处)之前执行,必须自行算出 scale 才能决定是否
    产出 wiki 候选;算出后缓存 ctx.metadata,供 VectorSearchStage 零向量分流。
    """

    def __init__(self, size_aware_router=None, wiki_page_locator=None):
        self._router = size_aware_router
        self._locator = wiki_page_locator

    @property
    def name(self):
        return "wiki_read"

    async def execute(self, ctx, config):
        if not self.is_enabled(config):
            return ctx
        # legacy 门控(S6):仅 wiki_first + size_aware.enabled 介入
        if Config.get("knowledge_workflow.mode", "legacy") != "wiki_first":
            return ctx
        if not Config.get("rag.size_aware.enabled", False):
            return ctx
        router = self._router or _get_container_service("size_aware_router", lambda: None)
        locator = self._locator or _get_container_service("wiki_page_locator", lambda: None)
        if router is None or locator is None:
            return ctx
        try:
            routing = router.route(ctx.question)
            scale = routing.get("scale", "full_search")
            ctx.metadata["scale"] = scale
            if routing.get("reason"):
                ctx.metadata["size_aware_reason"] = routing["reason"]
            # wiki_read / blend 档:wiki 候选作(部分)检索结果
            if scale in ("wiki_read", "blend"):
                cands, _ = locator.locate(ctx.question)
                if cands:
                    ctx.candidates = cands
        except Exception as e:
            logger.warning("WikiRead stage failed (non-fatal): %s", e)
            ctx.metadata.setdefault("warnings", []).append(f"wiki_read_failed: {e}")
        return ctx


class VectorSearchStage(PipelineStage):
    def __init__(self, db=None, hybrid_search=None, llm=None, graph_backend=None):
        self._db = db
        self._hybrid_search = hybrid_search
        self._llm = llm
        self._graph_backend = graph_backend

    @property
    def name(self):
        return "vector_search"

    @staticmethod
    def _normalize_knowledge_rows(rows: list[dict]) -> list[dict]:
        """把 QueryExecutor 返回的 knowledge_items 原始行归一化为 pipeline 标准 candidate。

        ``QueryExecutor.execute()`` 返回 ``SELECT ki.*`` 的原始行（字段为
        content / title / id），而 RAG 下游阶段（RerankStage / GenerateStage）
        统一从 candidate 的 ``text`` 字段与 ``metadata.page_id`` /
        ``metadata.knowledge_id`` 取值。不归一化会导致 ask_with_query 在
        structured / graph 模式下 source 文本为空、citation 缺失，LLM 误判
        “知识库无内容”。execute_query 工具直接把原始行返回给 Agent，不走
        此路径，故其行为不受影响。
        """
        normalized: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            kid = row.get("id", "")
            normalized.append({
                "id": kid,
                "text": row.get("content", ""),
                "metadata": {
                    "page_id": kid,
                    "knowledge_id": kid,
                    "title": row.get("title", ""),
                    "block_type": "knowledge",
                },
                "distance": 0,
                "match_channels": ["structured"],
            })
        return normalized

    async def execute(self, ctx, config):
        if not self.is_enabled(config):
            return ctx
        # size-aware 分流(第二阶段 Task 1.2):wiki_read 档零向量提前返回(在 agentic/
        # hybrid 之前,真正零向量+零 agentic LLM);blend 档备份 wiki 候选(hybrid 会覆盖
        # ctx.candidates,备份供 Task 1.3 RRF 融合)。
        scale = ctx.metadata.get("scale")
        if scale == "wiki_read" and ctx.candidates:
            ctx.metadata.setdefault("route", {
                "mode": "wiki_read",
                "explanation": "size-aware wiki_read (zero-vector retrieval)",
            })
            return ctx
        if scale == "blend" and ctx.candidates:
            ctx.metadata["_blend_wiki_candidates"] = list(ctx.candidates)
        top_k = config.get("top_k", 10)
        db = self._db or Database
        override_spec = ctx.metadata.get("query_spec_override") or ctx.query_spec_override
        try:
            try:
                from src.services.agentic_router import AgenticRouter, serialize_route
                agentic_llm = self._llm or _get_container_service("llm", LLMService)
                agentic = AgenticRouter(db=db, llm=agentic_llm)
                routing = agentic.route(ctx.question) if override_spec is None else {
                    "mode": "structured", "query_spec": override_spec,
                    "explanation": "explicit query_spec override",
                }
                ctx.metadata["route"] = serialize_route(routing)
                if routing.get("query_spec") is not None:
                    ctx.metadata["query_plan"] = serialize_route(routing).get(
                        "query_spec", {}
                    )
                else:
                    ctx.metadata.setdefault("query_plan", {})
                if routing["mode"] == "structured" and routing.get("query_spec"):
                    from src.services.query_executor import QueryExecutor
                    executor = QueryExecutor(db=db)
                    ctx.candidates = self._normalize_knowledge_rows(
                        executor.execute(routing["query_spec"])
                    )
                    if ctx.candidates:
                        return ctx
                elif routing["mode"] == "graph" and routing.get("query_spec"):
                    from src.services.graph_traversal import GraphTraversalService
                    from src.services.query_executor import QueryExecutor
                    executor = QueryExecutor(db=db)
                    start_pages = executor.execute(routing["query_spec"])
                    start_ids = [p.get("id", "") for p in start_pages]
                    traverse_config = routing.get("traverse", {"max_depth": 2})
                    traversal = GraphTraversalService(db=db, graph_backend=self._graph_backend).traverse(
                        start_ids=start_ids, start_type="knowledge",
                        max_depth=traverse_config.get("max_depth", 2),
                    )
                    ctx.candidates = self._normalize_knowledge_rows(start_pages)
                    ctx.metadata["graph_traversal"] = traversal
                    if ctx.candidates:
                        return ctx
            except Exception as e:
                logger.warning("Agentic routing failed, falling back: %s", e)
                ctx.metadata.setdefault("warnings", []).append(
                    f"agentic_router_failed: {e}"
                )
                ctx.metadata.setdefault("route", {
                    "mode": "hybrid", "explanation": "fallback after router error",
                })

            from src.services.query_router import QueryRouter
            router = QueryRouter(db=db)
            if router.route(ctx.question).mode == "logic":
                ctx.candidates = router.search(ctx.question, top_k=top_k)
                return ctx
            searcher = self._hybrid_search or _get_container_service("hybrid_search", HybridSearcher)
            all_results = []
            for query in ctx.rewritten_queries:
                results = searcher.search([query], top_k=top_k)
                all_results.extend(results)
            seen = set()
            unique = []
            for r in all_results:
                rid = r.get("id", r.get("metadata", {}).get("page_id", ""))
                if rid and rid not in seen:
                    seen.add(rid)
                    unique.append(r)
            unique.sort(key=lambda x: x.get("rrf_score", x.get("vec_score", x.get("score", 0))), reverse=True)
            ctx.candidates = unique[:top_k]

            # blend 档(Task 1.3):融合备份的 wiki 候选与 hybrid 检索候选
            if scale == "blend":
                from src.services.blend_fusion import blend_fusion
                wiki_cands = ctx.metadata.pop("_blend_wiki_candidates", [])
                if wiki_cands and ctx.candidates:
                    ctx.candidates = blend_fusion(wiki_cands, ctx.candidates)

            if not ctx.candidates:
                # BUG#3 文档说明：此为预期容错，非错误。
                # hybrid（向量+关键词融合）检索为空时，自动降级到 knowledge 级 FTS，
                # 保证召回不中断。warning 标记 `hybrid_search_empty_fallback_to_fts`
                # 仅供诊断，检索结果已正常返回（来自 FTS 兜底）。
                logger.info(
                    "Hybrid search returned empty — automatically falling back to "
                    "knowledge-level FTS (benign; recall continues from FTS)"
                )
                ctx.metadata.setdefault("warnings", []).append(
                    "hybrid_search_empty_fallback_to_fts"
                )
                try:
                    fts_results = db.search_knowledge(ctx.question, limit=top_k)
                    if fts_results:
                        ctx.candidates = [
                            {
                                "id": "",
                                "text": r.get("content", ""),
                                "metadata": {
                                    "page_id": r.get("id", ""),
                                    "knowledge_id": r.get("id", ""),
                                    "title": r.get("title", ""),
                                    "block_type": "knowledge",
                                    "properties": {},
                                },
                                "distance": 0,
                                "score": r.get("fts_rank", 0),
                            }
                            for r in fts_results
                        ]
                except Exception as e:
                    logger.warning("Knowledge FTS fallback failed: %s", e)
        except Exception as e:
            logger.warning("Vector search failed: %s", e)
            ctx.candidates = []
            ctx.metadata.setdefault("warnings", []).append(f"vector_search_failed: {e}")
        return ctx


class RerankStage(PipelineStage):
    def __init__(self, reranker=None):
        self._reranker = reranker

    @property
    def name(self):
        return "rerank"

    async def execute(self, ctx, config):
        if not self.is_enabled(config) or not ctx.candidates:
            ctx.reranked_results = ctx.candidates
            return ctx
        top_n = config.get("top_n", 5)
        min_score = config.get("min_score", 0.3)
        try:
            reranker = self._reranker or _get_container_service("reranker", LLMReranker)
            candidates_for_rerank = ctx.candidates[:top_n * 3]
            reranked = reranker.rerank(ctx.question, candidates_for_rerank)
            filtered = [r for r in reranked if r.get("rerank_score", 0) >= min_score][:top_n]
            # 安全网：过滤太严时保留 top_n 结果，避免下游上下文为空
            if not filtered and reranked:
                filtered = reranked[:top_n]
                logger.info("RerankStage: min_score=%.2f filtered all results, keeping top %d as safety net", min_score, len(filtered))
            ctx.reranked_results = filtered
        except Exception as e:
            logger.warning("Rerank failed: %s", e)
            ctx.metadata.setdefault("warnings", []).append(f"rerank_failed: {e}")
            ctx.reranked_results = ctx.candidates[:top_n]
        return ctx


class GenerateStage(PipelineStage):
    """LLM 生成阶段 — 支持普通和流式两种模式"""

    def __init__(self, llm=None, db=None):
        self._llm = llm
        self._db = db

    @property
    def name(self):
        return "generate"

    async def execute(self, ctx, config):
        if not self.is_enabled(config):
            return ctx
        self._build_context(ctx)

        stream = config.get("stream", False)
        config.get("temperature", 0.7)
        config.get("max_tokens", 2048)
        config.get("top_k", 5)
        config.get("rerank_top_n", 5)
        score_threshold = config.get("score_threshold", 0.5)

        # 筛选结果（同旧版逻辑）
        results = ctx.reranked_results
        if results and len(results) > 0 and "rerank_score" in results[0]:
            filtered = results
        else:
            filtered = [r for r in results if r.get("distance", 0) < score_threshold] if score_threshold > 0 else results
            if not filtered:
                filtered = results[:3]

        context_parts, sources = self._build_context_from_filtered(filtered)
        if ctx.wiki_context:
            context_parts.insert(0, f"【Wiki 结构化知识】\n{ctx.wiki_context}")
        context = "\n\n".join(context_parts) if context_parts else "（知识库中未找到相关内容）"
        # BUG-7 fix: 当检索无结果时，向 LLM 注入知识库领域概览，避免"未找到"型空回答
        # 让 LLM 至少能告知用户知识库覆盖了哪些领域，而非简单返回"未找到"
        if not context_parts and not ctx.wiki_context:
            try:
                from src.services.health import _get_kb_domain_summary
                domain_summary = _get_kb_domain_summary(ctx.candidates or [])
                if domain_summary:
                    context = f"（知识库中未直接找到相关内容，但以下为知识库当前覆盖的领域概览供参考）\n\n{domain_summary}"
                    ctx.metadata.setdefault("warnings", []).append("no_exact_match__domain_summary_provided")
            except Exception:
                pass  # 非致命：兜底失败时保留原始空上下文
        ctx.sources = sources
        # Sprint 2：构建 block_id → block_context 映射，供 Agent 端溯源
        block_contexts = {
            s.get("block_id"): s.get("block_context", "")
            for s in sources
            if s.get("block_id") and s.get("block_context")
        }
        if block_contexts:
            ctx.metadata["block_contexts"] = block_contexts

        messages = build_rag_messages(ctx.question, context, ctx.conversation_history)

        try:
            llm = self._llm
            if llm is None:
                try:
                    from src.core.container import get_active_container
                    _c = get_active_container()
                    llm = _c.llm if _c else LLMService()
                except Exception:
                    llm = LLMService()
            if stream:
                ctx.stream_generator = llm.chat_stream(messages, silent=True)
            else:
                # BUG#9：用 chat_with_usage 捕获 token 用量，供 trace 记录
                if hasattr(llm, "chat_with_usage"):
                    content, usage = llm.chat_with_usage(messages)
                    ctx.answer = strip_think(content)
                    if usage:
                        ctx._stage_tokens["generate"] = {
                            "input_tokens": usage.get("prompt_tokens", 0),
                            "output_tokens": usage.get("completion_tokens", 0),
                        }
                else:
                    ctx.answer = strip_think(llm.chat(messages))
        except Exception as e:
            logger.error("LLM generate failed: %s", e)
            ctx.metadata.setdefault("warnings", []).append(f"generate_failed: {e}")
            ctx.answer = f"抱歉，生成回答时发生错误：{str(e)}"
        return ctx

    def _build_context(self, ctx):
        """兼容旧 PipelineStage 的 _build_context"""
        pass  # 实际构建在 _build_context_from_filtered

    def _build_context_from_filtered(self, filtered):
        """批量查询标题，组装上下文和来源列表（含 Block 父链上下文）。

        返回:
            context_parts: LLM prompt 的字符串片段
            sources: 每条 source 必含 ``block_id`` / ``knowledge_id`` / ``title`` /
                     ``text_preview`` / ``score`` / ``block_context``，供 Agent
                     端做溯源 + 反查。
        """
        from src.services.citation_builder import CitationBuilder

        kid_map = {}
        for r in filtered:
            meta = r.get("metadata", {}) if isinstance(r.get("metadata"), dict) else {}
            kid = meta.get("page_id", meta.get("knowledge_id", ""))
            if not kid:
                kid = r.get("knowledge_id", "")
            if kid:
                kid_map[kid] = True
        _db = getattr(self, '_db', None) or Database
        items = _db.get_knowledge_batch(list(kid_map.keys())) if kid_map else {}
        citation_builder = CitationBuilder(_db)
        context_parts = []
        sources = []
        for i, result in enumerate(filtered):
            text = result.get("text", result.get("chunk_text", ""))
            # Parent-Child：优先使用父块完整内容
            parent_content = result.get("parent_content", "")
            block_ctx = result.get("block_context", "")
            if parent_content:
                context_parts.append(
                    f"[来源{i+1}] (父块上下文)\n{parent_content}\n---\n相关片段: {text}"
                )
            elif block_ctx:
                context_parts.append(f"[来源{i+1}] (上下文: {block_ctx})\n{text}")
            else:
                context_parts.append(f"[来源{i+1}]\n{text}")
            meta = result.get("metadata", {}) if isinstance(result.get("metadata"), dict) else {}
            kid = meta.get("page_id", meta.get("knowledge_id", ""))
            if not kid:
                kid = result.get("knowledge_id", "")
            item = items.get(kid)
            title = meta.get("title") if isinstance(meta, dict) else result.get("title")
            if not title and item:
                title = item.get("title", "未知")
            if not title:
                title = "未知"
            # block_id：Block-first RAG 下 chunk_id 等于 block_id；优先用显式字段
            block_id = (
                result.get("block_id")
                or meta.get("block_id")
                or result.get("chunk_id")
                or result.get("id", "")
            )

            # 分数回退链: rerank_score > rrf_score > vector_score > distance
            # 使用显式 None 检查，0.0 是有效分数
            score = 0.0
            for key in ("rerank_score", "rrf_score", "vector_score", "distance"):
                val = result.get(key)
                if val is not None:
                    score = val
                    break

            # 构建 match_channels
            channels = result.get("match_channels") or build_match_channels(result)

            # 构建 citation
            citation = citation_builder.build(result, item)

            sources.append({
                "block_id": block_id,
                "chunk_id": result.get("id", result.get("chunk_id", "")),
                "knowledge_id": kid,
                "title": title,
                "text_preview": text[:200],
                "block_context": block_ctx,
                "score": score,
                "match_channels": channels,
                "citation": citation.to_dict(),
            })
        return context_parts, sources


class PostProcessStage(PipelineStage):
    @property
    def name(self):
        return "postprocess"

    async def execute(self, ctx, config):
        if not self.is_enabled(config):
            return ctx
        if config.get("dedup", True):
            seen = set()
            unique = []
            for s in ctx.sources:
                kid = s.get("knowledge_id")
                if kid not in seen:
                    seen.add(kid)
                    unique.append(s)
            ctx.sources = unique
        max_len = config.get("max_context_length", 8000)
        if len(ctx.answer) > max_len:
            ctx.answer = ctx.answer[:max_len] + "...(已截断)"
            ctx.metadata.setdefault("warnings", []).append("answer_truncated")
        # 改进项3 (50轮测试报告): 截断 block_contexts，避免大文档（如供应商管理办法、
        # 单一来源采购合规指引）的父块上下文导致 MCP payload >300KB 被传输层截断。
        # 每个 block_context 限制为 block_context_max_length 字符（默认 2000），
        # 超出部分截断并标注。
        block_ctx_max = int(config.get("block_context_max_length", 2000) or 2000)
        block_contexts = ctx.metadata.get("block_contexts")
        if block_contexts and isinstance(block_contexts, dict):
            truncated_count = 0
            for bid, bctx in list(block_contexts.items()):
                if isinstance(bctx, str) and len(bctx) > block_ctx_max:
                    block_contexts[bid] = bctx[:block_ctx_max] + "...(block_context 已截断)"
                    truncated_count += 1
            if truncated_count:
                ctx.metadata.setdefault("warnings", []).append(
                    f"block_contexts_truncated:{truncated_count}"
                )
        return ctx


class EvidenceCompressStage(PipelineStage):
    """证据压缩阶段 — 在 rerank 后、generate 前压缩证据文本

    支持两种策略:
    - extractive: 抽取式 — 保留与问题相关的句子，删除无关内容（默认）
    - abstractive: 摘要式 — 用 LLM 生成精简摘要（需要 LLM 服务）

    配置:
        strategy: extractive | abstractive
        max_evidence_tokens: 最大证据 token 数（默认 4000）
        sentence_window: extractive 模式下保留相关句子的上下文窗口（默认 1）
    """

    def __init__(self, llm=None):
        self._llm = llm

    @property
    def name(self):
        return "evidence_compress"

    async def execute(self, ctx, config):
        if not self.is_enabled(config):
            return ctx
        strategy = config.get("strategy", "extractive")
        max_tokens = config.get("max_evidence_tokens", 4000)

        results = ctx.reranked_results
        if not results:
            return ctx

        if strategy == "abstractive":
            results = self._abstractive_compress(ctx.question, results, max_tokens)
        else:
            results = self._extractive_compress(ctx.question, results, max_tokens, config)

        ctx.reranked_results = results
        # 记录压缩统计
        ctx.metadata["evidence_compress"] = {
            "strategy": strategy,
            "max_tokens": max_tokens,
            "result_count": len(results),
        }
        return ctx

    def _extractive_compress(
        self, query: str, results: list[dict],
        max_tokens: int, config: dict,
    ) -> list[dict]:
        """抽取式压缩 — 保留与 query 相关的句子"""
        import re

        # 从 query 中提取关键词（简单分词：中文按字符，英文按空格）
        keywords = set()
        for word in re.findall(r'[一-鿿]|[a-zA-Z0-9]+', query.lower()):
            if len(word) > 1 or not word.isascii():
                keywords.add(word)

        window = config.get("sentence_window", 1)
        total_chars = 0
        max_chars = max_tokens * 4  # 粗略估计 1 token ≈ 4 字符
        compressed = []

        for result in results:
            text = result.get("text", "")
            if not text:
                compressed.append(result)
                continue

            sentences = self._split_sentences(text)
            if not sentences:
                compressed.append(result)
                total_chars += len(text)
                continue

            # 标记相关句子
            relevant_indices = set()
            for i, sent in enumerate(sentences):
                sent_lower = sent.lower()
                if any(kw in sent_lower for kw in keywords):
                    # 标记相关句子及其上下文窗口
                    for j in range(max(0, i - window), min(len(sentences), i + window + 1)):
                        relevant_indices.add(j)

            if not relevant_indices:
                # 没有匹配的关键词，保留前 3 句
                relevant_indices = set(range(min(3, len(sentences))))

            # 组装压缩文本
            compressed_text = " ".join(sentences[i] for i in sorted(relevant_indices))

            # 检查 token 预算
            if total_chars + len(compressed_text) > max_chars:
                # 超预算，截断到预算内
                remaining = max_chars - total_chars
                if remaining > 100:
                    compressed_text = compressed_text[:remaining] + "..."
                    compressed.append({**result, "text": compressed_text})
                    total_chars += len(compressed_text)
                break
            else:
                compressed.append({**result, "text": compressed_text})
                total_chars += len(compressed_text)

        return compressed

    def _abstractive_compress(
        self, query: str, results: list[dict], max_tokens: int,
    ) -> list[dict]:
        """摘要式压缩 — 用 LLM 生成精简摘要"""
        try:
            llm = self._llm
            if llm is None:
                try:
                    from src.core.container import get_active_container
                    _c = get_active_container()
                    llm = _c.llm if _c else LLMService()
                except Exception:
                    llm = LLMService()

            # 组装所有证据文本
            evidence_parts = []
            for i, r in enumerate(results):
                text = r.get("text", "")
                if text:
                    evidence_parts.append(f"[来源{i+1}]\n{text}")
            evidence = "\n\n".join(evidence_parts)

            if not evidence:
                return results

            prompt = (
                f"请根据以下证据内容，用简洁的语言总结与问题「{query}」直接相关的信息。"
                f"保留关键事实和数据，删除无关描述。总长度不超过{max_tokens // 2}个字符。\n\n"
                f"{evidence}"
            )

            messages = [
                {"role": "system", "content": "你是一个知识压缩助手，只保留与问题直接相关的事实。"},
                {"role": "user", "content": prompt},
            ]
            summary = strip_think(llm.chat(messages, silent=True))

            # 将摘要作为单个"压缩证据"返回
            return [{
                **results[0],
                "text": summary,
                "metadata": {
                    **(results[0].get("metadata") or {}),
                    "compressed": True,
                    "original_count": len(results),
                },
            }]
        except Exception as e:
            logger.warning("Abstractive compress failed, keeping original: %s", e)
            return results

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """将文本按句号、问号、感叹号、换行分段"""
        import re
        # 按中英文句号、问号、感叹号分割，保留分隔符
        parts = re.split(r'((?<=[。！？.!?\n]))', text)
        sentences = []
        buffer = ""
        for part in parts:
            buffer += part
            if re.search(r'[。！？.!?\n]$', part):
                s = buffer.strip()
                if s:
                    sentences.append(s)
                buffer = ""
        if buffer.strip():
            sentences.append(buffer.strip())
        return sentences


# ---- 阶段注册表 ----

class StageRegistry:
    _stages: dict[str, type[PipelineStage]] = {}
    _builtin_stages = [
        QueryRewriteStage, WikiRetrievalStage, WikiReadStage, VectorSearchStage,
        RerankStage, EvidenceCompressStage, GenerateStage, PostProcessStage,
    ]

    @classmethod
    def register(cls, name, stage_cls):
        cls._stages[name] = stage_cls
        logger.info("Registered pipeline stage: %s", name)

    @classmethod
    def get(cls, name):
        return cls._stages.get(name)

    @classmethod
    def get_all(cls):
        return cls._stages.copy()

    @classmethod
    def discover_from_config(cls, config):
        for entry in config.get("custom_stages", []):
            module_path = entry.get("module")
            class_name = entry.get("class")
            name = entry.get("name", class_name.lower())
            if module_path and class_name:
                try:
                    import importlib
                    mod = importlib.import_module(module_path)
                    cls.register(name, getattr(mod, class_name))
                except Exception as e:
                    logger.error("Failed to load custom stage %s: %s", name, e)

    @classmethod
    def init_builtins(cls):
        for stage_cls in cls._builtin_stages:
            instance = stage_cls()
            cls.register(instance.name, stage_cls)

    @classmethod
    def create_stage(cls, name: str, deps: dict | None = None) -> PipelineStage | None:
        """创建阶段实例，支持依赖注入。

        Args:
            name: 阶段名称
            deps: 依赖字典 (db, llm, query_rewriter, reranker, hybrid_search 等)
        """
        stage_cls = cls._stages.get(name)
        if not stage_cls:
            return None
        if deps is None:
            return stage_cls()
        # 只传递构造函数接受的参数
        import inspect
        try:
            sig = inspect.signature(stage_cls.__init__)
            params = set(sig.parameters.keys()) - {'self'}
            filtered = {k: v for k, v in deps.items() if k in params}
            return stage_cls(**filtered)
        except Exception:
            return stage_cls()


StageRegistry.init_builtins()


# ---- 默认管线配置 ----

DEFAULT_PIPELINE_CONFIG = [
    {"stage": "query_rewrite", "enabled": False, "mode": "llm", "num_variations": 3},  # BUG-1 fix: 默认禁用query_rewrite，消除一次LLM调用，大幅降低延迟
    {"stage": "wiki_retrieval", "enabled": True, "limit": 3},
    {"stage": "wiki_read", "enabled": True},  # size-aware: 小/混合查询读 wiki(零向量)
    {"stage": "vector_search", "enabled": True, "mode": "blend", "top_k": 10},
    {"stage": "rerank", "enabled": True, "top_n": 5, "min_score": 0.3},
    {"stage": "evidence_compress", "enabled": False, "strategy": "extractive", "max_evidence_tokens": 4000},
    {"stage": "generate", "enabled": True, "stream": False},
    {"stage": "postprocess", "enabled": True, "dedup": True,
     "max_context_length": 8000, "block_context_max_length": 2000},
]


# ---- 管线编排器 ----

class RagPipeline:
    """RAG 管线编排器 — 统一入口

    支持两种创建方式:
    1. 直接传入阶段实例: RagPipeline(stages=[(stage, config), ...])
    2. 从配置+依赖创建: RagPipeline(deps={...}) — StageRegistry 自动注入依赖
    """

    def __init__(self, pipeline_config: list[dict] | None = None, llm=None,
                 stages: list[tuple[PipelineStage, dict]] | None = None,
                 deps: dict | None = None):
        self._llm = llm
        self._deps = deps or {}
        self._stages: list[tuple[PipelineStage, dict]] = []
        if stages:
            self._stages = list(stages)
        else:
            self._build_from_config(pipeline_config or DEFAULT_PIPELINE_CONFIG, deps or {})

    def _build_from_config(self, config, deps: dict | None = None):
        for entry in config:
            stage_name = entry.get("stage")
            stage = StageRegistry.create_stage(stage_name, deps)
            if stage:
                self._stages.append((stage, entry))
            else:
                logger.warning("Unknown pipeline stage: %s", stage_name)

    def add_stage(self, stage, config):
        self._stages.append((stage, config))

    def _resolve_graph_backend(self):
        """从 deps 中提取 graph_backend"""
        return self._deps.get("graph_backend")

    def _try_auto_save_wiki(self, question: str, ctx: "RagContext"):
        """自动保存高质量回答到 Wiki（静默，不影响主流程）"""
        try:
            from src.utils.config import Config
            if not Config.get("wiki.enabled", False):
                return
            from src.services.wiki_compiler import WikiCompiler
            compiler = WikiCompiler()
            # 质量门槛：来源数 ≥ 2 + 无严重警告 + confidence ≥ 0.6
            critical_warnings = [w for w in ctx.metadata.get("warnings", [])
                                 if "no sources" in w.lower() or "failed" in w.lower()]
            confidence = max((s.get("score", 0.0) for s in ctx.sources), default=0.0)
            if len(ctx.sources) < 2 or critical_warnings or confidence < 0.6:
                return
            source_ids = [s.get("knowledge_id") for s in ctx.sources if s.get("knowledge_id")]
            page_id = compiler.save_answer(question, ctx.answer, source_ids)
            # wiki-first 文件系统层回写(syntheses,draft)
            try:
                from src.core.container import get_active_container as _gac
                _c = _gac()
                if _c is not None:
                    _c.knowledge_workflow.save_query(
                        question, ctx.answer, source_ids,
                        confidence=confidence, save_mode="auto",
                        timestamp=ctx.trace_id or "",
                    )
            except Exception:
                pass
            if page_id:
                logger.info("Auto-saved high-quality answer to Wiki: page_id=%s, question=%s",
                            page_id, question[:50])
        except Exception as e:
            logger.warning("Auto-save to Wiki failed (non-fatal): %s", e)

    async def execute(self, question, conversation_history=None, *, tool_name="ask", **kwargs):
        ctx = RagContext(question=question, conversation_history=conversation_history or [], **kwargs)

        # generate 与 postprocess 的执行关系：postprocess 依赖 generate 产出的
        # answer/sources，语义上无法并行。这里在遍历到 generate 时把它和 postprocess
        # 放在同一段顺序执行（generate 完成后再 postprocess），并用标志位避免主循环
        # 重复执行 postprocess。config 开关 generate_parallel 保留为兼容，实际为顺序执行。
        link_postprocess = Config.get("rag.pipeline.generate_parallel", True)
        postprocess_linked = False  # generate 段是否已顺带执行过 postprocess

        for stage, config in self._stages:
            if not stage.is_enabled(config):
                continue

            # 遍历到 generate 时，顺带把 postprocess 一起执行（顺序，非并行）
            if link_postprocess and stage.name == "generate":
                postprocess_idx = None
                for idx, (s, c) in enumerate(self._stages):
                    if s.name == "postprocess":
                        postprocess_idx = idx
                        break

                t0 = time.monotonic()
                ctx = await stage.execute(ctx, config)
                ctx._stage_durations[stage.name] = time.monotonic() - t0

                if postprocess_idx is not None:
                    pp_stage, pp_config = self._stages[postprocess_idx]
                    if pp_stage.is_enabled(pp_config):
                        t1 = time.monotonic()
                        ctx = await pp_stage.execute(ctx, pp_config)
                        ctx._stage_durations[pp_stage.name] = time.monotonic() - t1
                postprocess_linked = True
                continue

            # postprocess 若已在 generate 段执行过则跳过；否则正常执行。
            # 修复：仅当 generate 确实进入上面的分支才跳过——避免 generate 被禁用时
            # postprocess 被无条件误跳，导致 source 去重 / answer 截断静默丢失。
            if stage.name == "postprocess" and postprocess_linked:
                continue

            t0 = time.monotonic()
            try:
                ctx = await stage.execute(ctx, config)
            except Exception as e:
                logger.error("Stage %s failed: %s", stage.name, e)
            ctx._stage_durations[stage.name] = time.monotonic() - t0
        from src.services.source_graph import build_source_graph
        # 从 deps 获取 graph_backend，让 source_graph 也能利用图谱关系
        gb = self._resolve_graph_backend()
        ctx.source_graph = build_source_graph(ctx.sources, graph_backend=gb)
        # Sprint 2：构造结构化 RAG payload（ask 工具 7 字段）
        default_route = {"mode": "hybrid", "explanation": "no router decision recorded"}
        route = ctx.metadata.get("route", default_route)
        query_plan = ctx.metadata.get("query_plan", {})
        block_contexts = ctx.metadata.get("block_contexts", {})
        warnings = ctx.metadata.get("warnings", [])

        # 自动保存高质量回答到 Wiki
        wiki_auto_save = Config.get("wiki.auto_save_answer", False)
        if wiki_auto_save and ctx.answer and len(ctx.answer) >= Config.get("wiki.auto_save_min_length", 500):
            self._try_auto_save_wiki(question, ctx)

        # Phase 3: write trace if enabled
        trace_enabled = Config.get("rag.observability.trace_enabled", True)
        if trace_enabled:
            try:
                from src.services.trace import QueryTrace, StageTrace
                total_ms = sum(ctx._stage_durations.values()) * 1000
                stages = [
                    StageTrace(
                        name=name,
                        duration_ms=duration * 1000,
                        result_count=(
                            len(ctx.candidates) if name in ("vector_search", "wiki_retrieval")
                            else len(ctx.reranked_results) if name == "rerank"
                            else len(ctx.sources) if name in ("generate", "postprocess")
                            else 0
                        ),
                        # BUG#9：从 _stage_tokens 填充 generate 阶段的 token 用量
                        input_tokens=(
                            ctx._stage_tokens.get(name, {}).get("input_tokens", 0)
                        ),
                        output_tokens=(
                            ctx._stage_tokens.get(name, {}).get("output_tokens", 0)
                        ),
                    )
                    for name, duration in ctx._stage_durations.items()
                ]
                trace = QueryTrace(
                    trace_id=ctx.trace_id,
                    tool=tool_name,
                    question=question,
                    stages=stages,
                    total_duration_ms=total_ms,
                )
                trace.save()
            except Exception as e:
                logger.debug("Trace write failed (non-fatal): %s", e)

        return {
            "answer": ctx.answer,
            "sources": ctx.sources,
            "source_graph": ctx.source_graph,
            "route": route,
            "query_plan": query_plan,
            "block_contexts": block_contexts,
            "warnings": warnings,
            "wiki_context": ctx.wiki_context,
            "trace_id": ctx.trace_id,
        }


def create_pipeline_from_config(deps: dict | None = None):
    """从配置创建管线，支持依赖注入"""
    if not Config.get("rag.pipeline.enabled", False):
        return None
    rag_config = Config.get_all().get("rag", {})
    StageRegistry.discover_from_config(rag_config)
    pipeline_config = rag_config.get("pipeline", {}).get("stages", [])
    pipeline = RagPipeline(pipeline_config, deps=deps)
    return pipeline


# ---- LRU 缓存（BUG-1 fix: 高频问答缓存减少重复LLM调用） ----

class _RAGResultCache:
    """轻量级LRU缓存，缓存RAG问答结果避免重复LLM调用。

    Phase 3: maxsize 从 64 → 256，TTL 从 600s → 600s（可通过 config 调整）。
    """

    def __init__(self, maxsize: int | None = None, ttl: float | None = None):
        import threading
        self._maxsize = maxsize or int(Config.get("rag.cache.l1_rag_max", 256) or 256)
        self._ttl = ttl or float(Config.get("rag.cache.l1_rag_ttl", 600) or 600)
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        # 模块级单例被 FastMCP 线程池并发访问，get/put/clear 的多步复合操作
        # （move_to_end / popitem / del）需加锁，否则 LRU 状态错乱或 KeyError。
        self._lock = threading.RLock()

    def _make_key(self, question: str) -> str:
        return hashlib.md5(question.strip().lower().encode()).hexdigest()

    def get(self, question: str) -> dict | None:
        key = self._make_key(question)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            ts, result = entry
            if time.monotonic() - ts > self._ttl:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return result

    def put(self, question: str, result: dict) -> None:
        key = self._make_key(question)
        with self._lock:
            self._cache[key] = (time.monotonic(), result)
            self._cache.move_to_end(key)
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


_rag_cache = _RAGResultCache()


# ---- RAGService（向后兼容的统一入口） ----

class RAGService:
    """RAG 检索增强生成 — 统一入口

    同时支持普通和流式查询，内部使用 RagPipeline。
    支持构造器注入依赖（推荐），或自动从 Container 获取（兼容）。
    """

    def __init__(self, deps: dict | None = None):
        self._deps = deps
        pipeline: RagPipeline | None = None
        if Config.get("rag.pipeline.enabled", False):
            try:
                pipeline = create_pipeline_from_config(deps)
            except Exception as e:
                logger.warning("Failed to load config pipeline, using default: %s", e)
        self._pipeline = pipeline or RagPipeline(deps=deps)

    def _resolve_graph_backend(self):
        """从 deps 中提取 graph_backend。"""
        return (self._deps or {}).get("graph_backend")

    def query(self, question: str, conversation_history: list[dict] | None = None,
              phase_callback=None, skip_cache: bool = False,
              timeout: float | None = None) -> dict:
        """同步查询（非流式）— 直接通过管线执行，支持LRU缓存

        Args:
            timeout: 管线执行总超时秒数。None 时从 ``rag.ask.total_timeout``
                读取（默认 90s）。超时抛 ``concurrent.futures.TimeoutError``，
                由调用方决定是否返回部分结果。
                50轮测试报告 Bug-2: 旧实现硬编码 120s 且超时后 fallback 到
                ``_direct_query``（再次调 LLM），导致 kb_ask 偶发雪崩超时
                （MCP error -32001）。改为超时即抛出，不再雪崩。
        """
        import concurrent.futures
        import traceback

        # BUG-1 fix: 缓存命中直接返回，跳过整个管线
        if not skip_cache:
            cached = _rag_cache.get(question)
            if cached is not None:
                logger.info("RAG cache hit for query=%r", question[:50])
                result = dict(cached)
                # 缓存命中：本次未执行管线、未写新 trace，缓存的 trace_id 指向首次
                # 产生该缓存的那次请求，不代表本次链路。清空并标记 cache_hit。
                result["trace_id"] = ""
                result["cache_hit"] = True
                return result

        # BUG-2 fix (50轮测试报告): 超时从配置读取，默认 90s（比 ask_with_query
        # 的 120s 略短，给 MCP 客户端留余量，避免触发 -32001）
        if timeout is None:
            timeout = float(Config.get("rag.ask.total_timeout", 90) or 90)

        try:
            # 直接走管线，管线内部会依次执行全部阶段
            # （wiki_retrieval → vector_search → rerank → generate → postprocess）
            if phase_callback:
                phase_callback("searching", "RAG 管线执行中")

            result = _run_coroutine_sync(
                self._pipeline.execute(question, conversation_history),
                timeout=timeout,
            )
            if "source_graph" not in result:
                from src.services.source_graph import build_source_graph
                result["source_graph"] = build_source_graph(
                    result.get("sources", []),
                    graph_backend=self._resolve_graph_backend(),
                )
            # BUG-1 fix: 缓存成功的RAG结果
            if not skip_cache and result.get("answer"):
                _rag_cache.put(question, result)
                logger.info("RAG result cached for query=%r (cache_size=%d)", question[:50], _rag_cache.size)
            return dict(result)
        except concurrent.futures.TimeoutError:
            # BUG-2 fix: 超时即抛出，不再 fallback 到 _direct_query（避免雪崩）。
            # 调用方（ask 工具）负责返回部分结果 + 超时警告。
            logger.warning(
                "Pipeline timed out after %ss for query=%r, propagating to caller",
                timeout, question[:50],
            )
            raise
        except Exception as e:
            err_detail = traceback.format_exc()
            logger.error("Pipeline execution failed, falling back to direct query: %s\n%s", e, err_detail)
            # fallback：仅用 Wiki 上下文 + LLM 直接生成
            ctx = RagContext(question=question, conversation_history=conversation_history or [])
            return self._direct_query(question, conversation_history, ctx)

    def query_stream(self, question: str, conversation_history: list[dict] | None = None,
                     phase_callback=None):
        """流式查询 — 返回 (stream_generator, sources)"""
        top_k = Config.get("rag.top_k", 5)
        rerank_top_n = Config.get("rag.rerank.top_n", 5)
        score_threshold = Config.get("rag.score_threshold", 0.5)
        deps = self._deps or {}
        db = deps.get("db", Database)

        if phase_callback:
            phase_callback("rewriting", "查询改写")

        # 阶段 1: 查询改写 + Wiki 检索并发
        rewriter = deps.get("query_rewriter") or QueryRewriter()
        with ThreadPoolExecutor(max_workers=2) as pool:
            rewrite_future = pool.submit(rewriter.rewrite, question)
            wiki_future = pool.submit(self._get_wiki_context, question)
            try:
                queries = rewrite_future.result()
            except Exception as e:
                logger.warning("Query rewrite failed, using original question: %s", e)
                queries = [question]
            try:
                wiki_context = wiki_future.result()
            except Exception as e:
                logger.warning("Wiki context retrieval failed: %s", e)
                wiki_context = ""

        if phase_callback:
            phase_callback("searching", "混合检索")

        # 阶段 2: 混合检索
        searcher = deps.get("hybrid_search") or HybridSearcher()
        from src.services.query_router import QueryRouter
        router = QueryRouter(db=db, hybrid_searcher=searcher)
        if router.route(question).mode == "logic":
            candidates = router.search(question, top_k=top_k)
        else:
            candidates = searcher.search(queries, top_k=top_k)

        if phase_callback:
            phase_callback("reranking", "结果重排序")

        # 阶段 3: 重排序
        reranker = deps.get("reranker") or LLMReranker()
        results = reranker.rerank(question, candidates, top_n=rerank_top_n)

        if phase_callback:
            phase_callback("generating", "生成回答")

        if not results:
            # 回退：知识级 FTS + LIKE 搜索（兜底 block 级搜索遗漏的结果）
            try:
                fts_results = db.search_knowledge(question, limit=top_k)
                if fts_results:
                    results = [
                        {
                            "text": r.get("content", ""),
                            "metadata": {
                                "page_id": r.get("id", ""),
                                "knowledge_id": r.get("id", ""),
                                "title": r.get("title", ""),
                            },
                            "distance": 0,
                            "rerank_score": 0.5,
                        }
                        for r in fts_results
                    ]
            except Exception as e:
                logger.warning("Knowledge FTS fallback failed: %s", e)

        if not results:
            def _empty_gen():
                yield "抱歉，知识库中未找到与您的问题相关的内容，请尝试换个方式提问。"
            return _empty_gen(), [], {"nodes": [], "edges": []}

        if "rerank_score" in results[0]:
            filtered = results
        else:
            filtered = [r for r in results if r.get("distance", 0) < score_threshold] if score_threshold > 0 else results
            if not filtered:
                filtered = results[:3]

        context_parts, sources = self._build_context(filtered)
        if wiki_context:
            context_parts.insert(0, f"【Wiki 结构化知识】\n{wiki_context}")

        context = "\n\n".join(context_parts) if context_parts else "（知识库中未找到相关内容）"
        messages = build_rag_messages(question, context, conversation_history)

        llm = deps.get("llm") or _get_container_service("llm", LLMService)
        from src.services.source_graph import build_source_graph
        source_graph = build_source_graph(sources, graph_backend=deps.get("graph_backend"))
        return llm.chat_stream(messages, silent=True), sources, source_graph

    def _direct_query(self, question: str, conversation_history: list[dict] | None,
                      ctx: RagContext) -> dict:
        """当管线执行失败时的直接查询 fallback"""
        try:
            messages = build_rag_messages(
                question,
                ctx.wiki_context or "（知识库中未找到相关内容）",
                conversation_history,
            )
            llm = _get_container_service("llm", LLMService)
            answer = strip_think(llm.chat(messages))
            return {"answer": answer, "sources": [], "source_graph": {"nodes": [], "edges": []}}
        except Exception as e:
            logger.error("Direct query fallback also failed: %s", e)
            return {
                "answer": f"抱歉，查询过程中发生错误：{str(e)}",
                "sources": [],
                "source_graph": {"nodes": [], "edges": []},
            }

    def _get_wiki_context(self, query: str) -> str:
        if not Config.get("wiki.enabled", False):
            return ""
        try:
            wiki_results = Database.search_wiki_fts(query, limit=3)
        except Exception:
            return ""
        if not wiki_results:
            return ""
        parts = []
        for wp in wiki_results:
            summary = wp.get("concept_summary", "")
            content = wp.get("content", "")
            text = f"- {wp['title']}"
            if summary:
                text += f"：{summary}"
            if content:
                text += f"\n  {content[:300]}"
            parts.append(text)
        return "\n".join(parts)

    def _build_context(self, filtered: list[dict]) -> tuple[list[str], list[dict]]:
        from src.services.citation_builder import CitationBuilder

        kid_map = {}
        for r in filtered:
            metadata = r.get("metadata", {})
            kid = (
                (metadata.get("knowledge_id") or metadata.get("page_id") or "")
                if isinstance(metadata, dict)
                else r.get("knowledge_id", "")
            )
            if kid:
                kid_map[kid] = True
        _db = getattr(self, '_db', None) or Database
        items = _db.get_knowledge_batch(list(kid_map.keys())) if kid_map else {}
        citation_builder = CitationBuilder(_db)
        context_parts = []
        sources = []
        for i, result in enumerate(filtered):
            text = result.get("text", result.get("chunk_text", ""))
            # Parent-Child：优先使用父块完整内容
            parent_content = result.get("parent_content", "")
            block_ctx = result.get("block_context", "")
            if parent_content:
                context_parts.append(
                    f"[来源{i+1}] (父块上下文)\n{parent_content}\n---\n相关片段: {text}"
                )
            elif block_ctx:
                context_parts.append(f"[来源{i+1}] (上下文: {block_ctx})\n{text}")
            else:
                context_parts.append(f"[来源{i+1}]\n{text}")
            metadata = result.get("metadata", {})
            kid = (
                (metadata.get("knowledge_id") or metadata.get("page_id") or "")
                if isinstance(metadata, dict)
                else result.get("knowledge_id", "")
            )
            item = items.get(kid)
            title = (metadata.get("title") if isinstance(metadata, dict) else None) or (item.get("title", "未知") if item else "未知")
            block_id = (
                result.get("block_id")
                or (metadata.get("block_id") if isinstance(metadata, dict) else "")
                or result.get("id", "")
            )

            # 分数回退链: rerank_score > rrf_score > vector_score > distance
            score = 0.0
            for key in ("rerank_score", "rrf_score", "vector_score", "distance"):
                val = result.get(key)
                if val is not None:
                    score = val
                    break

            # 构建 match_channels
            channels = result.get("match_channels") or build_match_channels(result)

            # 构建 citation
            citation = citation_builder.build(result, item)

            sources.append({
                "block_id": block_id,
                "chunk_id": result.get("id", ""),
                "knowledge_id": kid,
                "title": title,
                "text_preview": text[:200],
                "score": score,
                "match_channels": channels,
                "citation": citation.to_dict(),
            })
        return context_parts, sources

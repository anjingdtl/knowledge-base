"""RAG 管线抽象与可配置阶段 — 统一入口

这是唯一的 RAG 实现，旧版 rag.py 已合并到此文件。
RAGService 类保留为向后兼容的别名。
"""
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional, Callable
import json

from src.utils.config import Config
from src.services.db import Database
from src.services.hybrid_search import HybridSearcher
from src.services.query_rewriter import QueryRewriter
from src.services.reranker import LLMReranker
from src.services.llm import LLMService
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


# ---- 阶段抽象基类 ----

class PipelineStage(ABC):
    """管线阶段抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    def is_enabled(self, config: dict) -> bool:
        return config.get("enabled", True)

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


class VectorSearchStage(PipelineStage):
    def __init__(self, db=None, hybrid_search=None, llm=None):
        self._db = db
        self._hybrid_search = hybrid_search
        self._llm = llm

    @property
    def name(self):
        return "vector_search"

    async def execute(self, ctx, config):
        if not self.is_enabled(config):
            return ctx
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
                    ctx.candidates = executor.execute(routing["query_spec"])
                    if ctx.candidates:
                        return ctx
                elif routing["mode"] == "graph" and routing.get("query_spec"):
                    from src.services.query_executor import QueryExecutor
                    from src.services.graph_traversal import GraphTraversalService
                    executor = QueryExecutor(db=db)
                    start_pages = executor.execute(routing["query_spec"])
                    start_ids = [p["id"] for p in start_pages]
                    traverse_config = routing.get("traverse", {"max_depth": 2})
                    traversal = GraphTraversalService(db=db).traverse(
                        start_ids=start_ids, start_type="knowledge",
                        max_depth=traverse_config.get("max_depth", 2),
                    )
                    ctx.candidates = start_pages
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

            if not ctx.candidates:
                logger.info("Hybrid search returned empty, falling back to knowledge-level FTS")
                ctx.metadata.setdefault("warnings", []).append("hybrid_search_empty_fallback_to_fts")
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
            ctx.reranked_results = [r for r in reranked if r.get("score", 0) >= min_score][:top_n]
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
        temperature = config.get("temperature", 0.7)
        max_tokens = config.get("max_tokens", 2048)
        top_k = config.get("top_k", 5)
        rerank_top_n = config.get("rerank_top_n", 5)
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
        context_parts = []
        sources = []
        for i, result in enumerate(filtered):
            text = result.get("text", result.get("chunk_text", ""))
            # 附加 Block 父链上下文
            block_ctx = result.get("block_context", "")
            if block_ctx:
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
            sources.append({
                "block_id": block_id,
                "chunk_id": result.get("id", result.get("chunk_id", "")),
                "knowledge_id": kid,
                "title": title,
                "text_preview": text[:200],
                "block_context": block_ctx,
                "score": result.get("rerank_score", result.get("rrf_score", result.get("score", result.get("distance", 0)))),
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
        return ctx


# ---- 阶段注册表 ----

class StageRegistry:
    _stages: dict[str, type[PipelineStage]] = {}
    _builtin_stages = [
        QueryRewriteStage, WikiRetrievalStage, VectorSearchStage,
        RerankStage, GenerateStage, PostProcessStage,
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
    {"stage": "query_rewrite", "enabled": True, "mode": "llm", "num_variations": 3},
    {"stage": "wiki_retrieval", "enabled": True, "limit": 3},
    {"stage": "vector_search", "enabled": True, "mode": "blend", "top_k": 10},
    {"stage": "rerank", "enabled": True, "top_n": 5, "min_score": 0.3},
    {"stage": "generate", "enabled": True, "stream": False},
    {"stage": "postprocess", "enabled": True, "dedup": True},
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

    async def execute(self, question, conversation_history=None, **kwargs):
        ctx = RagContext(question=question, conversation_history=conversation_history or [], **kwargs)
        for stage, config in self._stages:
            if stage.is_enabled(config):
                try:
                    ctx = await stage.execute(ctx, config)
                except Exception as e:
                    logger.error("Stage %s failed: %s", stage.name, e)
        from src.services.source_graph import build_source_graph
        ctx.source_graph = build_source_graph(ctx.sources)
        # Sprint 2：构造结构化 RAG payload（ask 工具 7 字段）
        default_route = {"mode": "hybrid", "explanation": "no router decision recorded"}
        route = ctx.metadata.get("route", default_route)
        query_plan = ctx.metadata.get("query_plan", {})
        block_contexts = ctx.metadata.get("block_contexts", {})
        warnings = ctx.metadata.get("warnings", [])
        return {
            "answer": ctx.answer,
            "sources": ctx.sources,
            "source_graph": ctx.source_graph,
            "route": route,
            "query_plan": query_plan,
            "block_contexts": block_contexts,
            "warnings": warnings,
            "wiki_context": ctx.wiki_context,
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


# ---- RAGService（向后兼容的统一入口） ----

class RAGService:
    """RAG 检索增强生成 — 统一入口

    同时支持普通和流式查询，内部使用 RagPipeline。
    支持构造器注入依赖（推荐），或自动从 Container 获取（兼容）。
    """

    def __init__(self, deps: dict | None = None):
        self._deps = deps
        self._pipeline = None
        if Config.get("rag.pipeline.enabled", False):
            try:
                self._pipeline = create_pipeline_from_config(deps)
            except Exception as e:
                logger.warning("Failed to load config pipeline, using default: %s", e)
        if not self._pipeline:
            self._pipeline = RagPipeline(deps=deps)

    def query(self, question: str, conversation_history: list[dict] | None = None,
              phase_callback=None) -> dict:
        """同步查询（非流式）— 直接通过管线执行，无冗余预处理"""
        import asyncio
        import concurrent.futures

        try:
            # 直接走管线，管线内部会依次执行全部阶段
            # （query_rewrite → wiki_retrieval → vector_search → rerank → generate → postprocess）
            if phase_callback:
                phase_callback("searching", "RAG 管线执行中")

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # 已有事件循环（streamable-http 等场景）— 线程安全提交
                future = asyncio.run_coroutine_threadsafe(
                    self._pipeline.execute(question, conversation_history), loop
                )
                result = future.result(timeout=120)
            else:
                # 无事件循环 → asyncio.run() 安全
                result = asyncio.run(self._pipeline.execute(
                    question, conversation_history
                ))
            if "source_graph" not in result:
                from src.services.source_graph import build_source_graph
                result["source_graph"] = build_source_graph(result.get("sources", []))
            return result
        except Exception as e:
            logger.error("Pipeline execution failed, falling back to direct query: %s", e)
            # fallback：仅用 Wiki 上下文 + LLM 直接生成
            ctx = RagContext(question=question, conversation_history=conversation_history or [])
            return self._direct_query(question, conversation_history, ctx)

    def query_stream(self, question: str, conversation_history: list[dict] | None = None,
                     phase_callback=None):
        """流式查询 — 返回 (stream_generator, sources)"""
        import asyncio

        top_k = Config.get("rag.top_k", 5)
        rerank_top_n = Config.get("rag.rerank.top_n", 5)
        score_threshold = Config.get("rag.score_threshold", 0.5)

        if phase_callback:
            phase_callback("rewriting", "查询改写")

        # 阶段 1: 查询改写 + Wiki 检索并发
        rewriter = QueryRewriter()
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
        searcher = HybridSearcher()
        from src.services.query_router import QueryRouter
        router = QueryRouter(db=Database, hybrid_searcher=searcher)
        if router.route(question).mode == "logic":
            candidates = router.search(question, top_k=top_k)
        else:
            candidates = searcher.search(queries, top_k=top_k)

        if phase_callback:
            phase_callback("reranking", "结果重排序")

        # 阶段 3: 重排序
        reranker = LLMReranker()
        results = reranker.rerank(question, candidates, top_n=rerank_top_n)

        if phase_callback:
            phase_callback("generating", "生成回答")

        if not results:
            # 回退：知识级 FTS + LIKE 搜索（兜底 block 级搜索遗漏的结果）
            try:
                fts_results = Database.search_knowledge(question, limit=top_k)
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

        llm = _get_container_service("llm", LLMService)
        from src.services.source_graph import build_source_graph
        source_graph = build_source_graph(sources)
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
        context_parts = []
        sources = []
        for i, result in enumerate(filtered):
            text = result.get("text", result.get("chunk_text", ""))
            # 附加 Block 父链上下文
            block_ctx = result.get("block_context", "")
            if block_ctx:
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
            sources.append({
                "block_id": block_id,
                "chunk_id": result.get("id", ""),
                "knowledge_id": kid,
                "title": title,
                "text_preview": text[:200],
                "score": result.get("rerank_score", result.get("distance", 0)),
            })
        return context_parts, sources

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

# ---- 统一的 prompt 模板 ----

RAG_SYSTEM_PROMPT = """你是一个知识库助手。请基于以下知识库内容回答用户的问题。

要求：
1. 优先使用知识库中的内容进行回答
2. 如果同时有「Wiki 结构化知识」和「原始文档片段」，优先采纳 Wiki 中已综合验证的知识
3. 如果知识库中没有相关信息，请明确说明，不要编造
4. 在回答中标注引用的知识来源
5. 用中文回答"""

RAG_USER_TEMPLATE = """知识库内容：
{context}

用户问题：{question}"""


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
    conversation_history: list[dict] = field(default_factory=list)
    stream_generator: Any = None  # 流式生成时的 generator
    metadata: dict = field(default_factory=dict)


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
            rewriter = QueryRewriter()
            ctx.rewritten_queries = rewriter.rewrite(ctx.question, num_variations=num_variations)
        except Exception as e:
            logger.warning("Query rewrite failed: %s", e)
            ctx.rewritten_queries = [ctx.question]
        return ctx


class WikiRetrievalStage(PipelineStage):
    @property
    def name(self):
        return "wiki_retrieval"

    async def execute(self, ctx, config):
        if not self.is_enabled(config):
            return ctx
        if not Config.get("wiki.enabled", False):
            return ctx

        limit = config.get("limit", 3)
        try:
            # 优先用 FTS 搜索（同旧版 rag.py 的 _get_wiki_context）
            wiki_results = Database.search_wiki_fts(ctx.question, limit=limit)
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
                # FTS 没结果时 fallback 到列表搜索
                pages = Database.list_wiki_pages(status="published", search=ctx.question, limit=limit)
                if pages:
                    ctx.wiki_context = "\n\n".join([
                        f"## {p['title']}\n{p.get('content', '')[:500]}" for p in pages
                    ])
        except Exception as e:
            logger.warning("Wiki retrieval failed: %s", e)
        return ctx


class VectorSearchStage(PipelineStage):
    @property
    def name(self):
        return "vector_search"

    async def execute(self, ctx, config):
        if not self.is_enabled(config):
            return ctx
        top_k = config.get("top_k", 10)
        try:
            searcher = HybridSearcher()
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
            unique.sort(key=lambda x: x.get("rrf_score", x.get("vec_score", 0)), reverse=True)
            ctx.candidates = unique[:top_k]
        except Exception as e:
            logger.warning("Vector search failed: %s", e)
            ctx.candidates = []
        return ctx


class RerankStage(PipelineStage):
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
            reranker = LLMReranker()
            candidates_for_rerank = ctx.candidates[:top_n * 3]
            reranked = reranker.rerank(ctx.question, candidates_for_rerank)
            ctx.reranked_results = [r for r in reranked if r.get("score", 0) >= min_score][:top_n]
        except Exception as e:
            logger.warning("Rerank failed: %s", e)
            ctx.reranked_results = ctx.candidates[:top_n]
        return ctx


class GenerateStage(PipelineStage):
    """LLM 生成阶段 — 支持普通和流式两种模式"""

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

        user_msg = RAG_USER_TEMPLATE.format(context=context, question=ctx.question)
        messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]
        if ctx.conversation_history:
            messages.extend(ctx.conversation_history[-6:])
        messages.append({"role": "user", "content": user_msg})

        try:
            llm = LLMService()
            if stream:
                ctx.stream_generator = llm.chat_stream(messages, silent=True)
            else:
                ctx.answer = strip_think(llm.chat(messages))
        except Exception as e:
            logger.error("LLM generate failed: %s", e)
            ctx.answer = f"抱歉，生成回答时发生错误：{str(e)}"
        return ctx

    def _build_context(self, ctx):
        """兼容旧 PipelineStage 的 _build_context"""
        pass  # 实际构建在 _build_context_from_filtered

    def _build_context_from_filtered(self, filtered):
        """批量查询标题，组装上下文和来源列表"""
        kid_map = {}
        for r in filtered:
            meta = r.get("metadata", {}) if isinstance(r.get("metadata"), dict) else {}
            kid = meta.get("page_id", meta.get("knowledge_id", ""))
            if not kid:
                kid = r.get("knowledge_id", "")
            if kid:
                kid_map[kid] = True
        items = Database.get_knowledge_batch(list(kid_map.keys())) if kid_map else {}
        context_parts = []
        sources = []
        for i, result in enumerate(filtered):
            text = result.get("text", result.get("chunk_text", ""))
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
            sources.append({
                "chunk_id": result.get("id", result.get("chunk_id", "")),
                "knowledge_id": kid,
                "title": title,
                "text_preview": text[:200],
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
    """RAG 管线编排器 — 统一入口"""

    def __init__(self, pipeline_config: list[dict] | None = None):
        self._stages: list[tuple[PipelineStage, dict]] = []
        self._build_from_config(pipeline_config or DEFAULT_PIPELINE_CONFIG)

    def _build_from_config(self, config):
        for entry in config:
            stage_name = entry.get("stage")
            stage_cls = StageRegistry.get(stage_name)
            if stage_cls:
                self._stages.append((stage_cls(), entry))
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
        return {"answer": ctx.answer, "sources": ctx.sources, "wiki_context": ctx.wiki_context}


def create_pipeline_from_config():
    """从配置创建管线"""
    if not Config.get("rag.pipeline.enabled", False):
        return None
    rag_config = Config.get_all().get("rag", {})
    StageRegistry.discover_from_config(rag_config)
    pipeline_config = rag_config.get("pipeline", {}).get("stages", [])
    pipeline = RagPipeline(pipeline_config)
    return pipeline


# ---- RAGService（向后兼容的统一入口） ----

class RAGService:
    """RAG 检索增强生成 — 统一入口

    同时支持普通和流式查询，内部使用 RagPipeline。
    保持与旧版 rag.py 完全兼容的 API。
    """

    def __init__(self):
        self._pipeline = None
        if Config.get("rag.pipeline.enabled", False):
            try:
                self._pipeline = create_pipeline_from_config()
            except Exception as e:
                logger.warning("Failed to load config pipeline, using default: %s", e)
        if not self._pipeline:
            self._pipeline = RagPipeline()

    def query(self, question: str, conversation_history: list[dict] | None = None,
              phase_callback=None) -> dict:
        """同步查询（非流式）— 直接通过管线执行，无冗余预处理"""
        import asyncio

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
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    result = ex.submit(asyncio.run, self._pipeline.execute(
                        question, conversation_history
                    )).result()
            else:
                result = asyncio.run(self._pipeline.execute(
                    question, conversation_history
                ))
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
            return _empty_gen(), []

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
        user_msg = RAG_USER_TEMPLATE.format(context=context, question=question)
        messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]
        if conversation_history:
            messages.extend(conversation_history[-6:])
        messages.append({"role": "user", "content": user_msg})

        llm = LLMService()
        return llm.chat_stream(messages, silent=True), sources

    def _direct_query(self, question: str, conversation_history: list[dict] | None,
                      ctx: RagContext) -> dict:
        """当管线执行失败时的直接查询 fallback"""
        try:
            user_msg = RAG_USER_TEMPLATE.format(
                context=ctx.wiki_context or "（知识库中未找到相关内容）",
                question=question,
            )
            messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]
            if conversation_history:
                messages.extend(conversation_history[-6:])
            messages.append({"role": "user", "content": user_msg})
            llm = LLMService()
            answer = strip_think(llm.chat(messages))
            return {"answer": answer, "sources": []}
        except Exception as e:
            logger.error("Direct query fallback also failed: %s", e)
            return {"answer": f"抱歉，查询过程中发生错误：{str(e)}", "sources": []}

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
            kid = metadata.get("knowledge_id", "") if isinstance(metadata, dict) else r.get("knowledge_id", "")
            if kid:
                kid_map[kid] = True
        items = Database.get_knowledge_batch(list(kid_map.keys())) if kid_map else {}
        context_parts = []
        sources = []
        for i, result in enumerate(filtered):
            text = result.get("text", result.get("chunk_text", ""))
            context_parts.append(f"[来源{i+1}]\n{text}")
            metadata = result.get("metadata", {})
            kid = metadata.get("knowledge_id", "") if isinstance(metadata, dict) else result.get("knowledge_id", "")
            item = items.get(kid)
            title = (metadata.get("title") if isinstance(metadata, dict) else None) or (item.get("title", "未知") if item else "未知")
            sources.append({
                "chunk_id": result.get("id", ""),
                "knowledge_id": kid,
                "title": title,
                "text_preview": text[:200],
                "score": result.get("rerank_score", result.get("distance", 0)),
            })
        return context_parts, sources

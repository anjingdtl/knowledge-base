"""RAG 管线抽象与可配置阶段"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Callable
import json

from src.utils.config import Config
from src.services.db import Database
from src.services.hybrid_search import HybridSearcher
from src.services.query_rewriter import QueryRewriter
from src.services.reranker import LLMReranker
from src.services.llm import LLMService

logger = logging.getLogger(__name__)


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
    metadata: dict = field(default_factory=dict)


class PipelineStage(ABC):
    """管线阶段抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """阶段名称"""
        pass

    def is_enabled(self, config: dict) -> bool:
        """检查阶段是否启用"""
        return config.get("enabled", True)

    @abstractmethod
    async def execute(self, ctx: RagContext, config: dict) -> RagContext:
        """执行阶段逻辑"""
        pass


class QueryRewriteStage(PipelineStage):
    """查询重写阶段"""

    @property
    def name(self) -> str:
        return "query_rewrite"

    async def execute(self, ctx: RagContext, config: dict) -> RagContext:
        mode = config.get("mode", "llm")
        if mode == "disabled" or not self.is_enabled(config):
            ctx.rewritten_queries = [ctx.question]
            return ctx

        num_variations = config.get("num_variations", 3)
        try:
            rewriter = QueryRewriter()
            queries = rewriter.rewrite(ctx.question, num_variations=num_variations)
            ctx.rewritten_queries = queries
        except Exception as e:
            logger.warning(f"Query rewrite failed: {e}")
            ctx.rewritten_queries = [ctx.question]
        return ctx


class WikiRetrievalStage(PipelineStage):
    """Wiki 知识检索阶段"""

    @property
    def name(self) -> str:
        return "wiki_retrieval"

    async def execute(self, ctx: RagContext, config: dict) -> RagContext:
        if not self.is_enabled(config):
            return ctx

        limit = config.get("limit", 3)
        try:
            # 使用 FTS 搜索 Wiki
            pages = Database.list_wiki_pages(status="published", search=ctx.question, limit=limit)
            if pages:
                wiki_text = "\n\n".join([
                    f"## {p['title']}\n{p.get('content', '')[:500]}"
                    for p in pages
                ])
                ctx.wiki_context = f"【Wiki 参考资料】\n{wiki_text}\n\n"
        except Exception as e:
            logger.warning(f"Wiki retrieval failed: {e}")
        return ctx


class VectorSearchStage(PipelineStage):
    """向量搜索阶段"""

    @property
    def name(self) -> str:
        return "vector_search"

    async def execute(self, ctx: RagContext, config: dict) -> RagContext:
        if not self.is_enabled(config):
            return ctx

        mode = config.get("mode", "blend")
        top_k = config.get("top_k", 10)

        try:
            searcher = HybridSearcher()
            # 使用重写后的查询进行搜索
            all_results = []
            for query in ctx.rewritten_queries:
                results = searcher.search(query, limit=top_k, mode=mode)
                all_results.extend(results)

            # 去重并按分数排序
            seen = set()
            unique_results = []
            for r in all_results:
                kid = r.get("knowledge_id")
                if kid and kid not in seen:
                    seen.add(kid)
                    unique_results.append(r)

            # 排序并限制数量
            unique_results.sort(key=lambda x: x.get("score", 0), reverse=True)
            ctx.candidates = unique_results[:top_k]

        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            ctx.candidates = []
        return ctx


class RerankStage(PipelineStage):
    """重排序阶段"""

    @property
    def name(self) -> str:
        return "rerank"

    async def execute(self, ctx: RagContext, config: dict) -> RagContext:
        if not self.is_enabled(config) or not ctx.candidates:
            ctx.reranked_results = ctx.candidates
            return ctx

        top_n = config.get("top_n", 5)
        min_score = config.get("min_score", 0.3)

        try:
            reranker = LLMReranker()
            candidates_for_rerank = ctx.candidates[:top_n * 3]  # 取更多候选
            reranked = reranker.rerank(ctx.question, candidates_for_rerank)

            # 过滤低分结果
            ctx.reranked_results = [r for r in reranked if r.get("score", 0) >= min_score][:top_n]

        except Exception as e:
            logger.warning(f"Rerank failed: {e}")
            ctx.reranked_results = ctx.candidates[:top_n]
        return ctx


class LLMGenerateStage(PipelineStage):
    """LLM 生成阶段"""

    @property
    def name(self) -> str:
        return "generate"

    async def execute(self, ctx: RagContext, config: dict) -> RagContext:
        if not self.is_enabled(config):
            return ctx

        # 构建上下文
        self._build_context(ctx)

        # 调用 LLM
        temperature = config.get("temperature", 0.7)
        max_tokens = config.get("max_tokens", 2048)

        try:
            llm = LLMService()
            system_prompt = self._get_system_prompt()
            user_prompt = self._get_user_prompt(ctx)

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            # 添加对话历史（最近几轮）
            for msg in ctx.conversation_history[-6:]:
                messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

            response = llm.chat(messages, temperature=temperature, max_tokens=max_tokens)
            ctx.answer = response

        except Exception as e:
            logger.error(f"LLM generate failed: {e}")
            ctx.answer = f"抱歉，生成回答时发生错误：{str(e)}"

        return ctx

    def _build_context(self, ctx: RagContext):
        """构建检索上下文"""
        chunks = []
        for r in ctx.reranked_results:
            chunk_text = r.get("chunk_text", "")
            title = r.get("title", "")
            score = r.get("score", 0)
            kid = r.get("knowledge_id", "")

            chunks.append(f"【来源 {len(chunks)+1}】{title} (相关度:{score:.2f})\n{chunk_text}")
            ctx.sources.append({
                "knowledge_id": kid,
                "title": title,
                "chunk_id": r.get("chunk_id", ""),
                "score": score,
            })

        ctx.context_text = "\n\n".join(chunks)

    def _get_system_prompt(self) -> str:
        return """你是一个专业的知识库问答助手。请根据提供的参考资料回答用户的问题。

要求：
1. 只根据提供的参考资料回答，不要编造信息
2. 如果参考资料中没有相关信息，请如实说明
3. 回答要简洁明了，条理清晰
4. 在回答中注明参考来源"""

    def _get_user_prompt(self, ctx: RagContext) -> str:
        parts = []
        if ctx.wiki_context:
            parts.append(ctx.wiki_context)
        if ctx.context_text:
            parts.append(f"【参考资料】\n{ctx.context_text}")
        parts.append(f"【用户问题】\n{ctx.question}")
        return "\n\n".join(parts)


class PostProcessStage(PipelineStage):
    """后处理阶段"""

    @property
    def name(self) -> str:
        return "postprocess"

    async def execute(self, ctx: RagContext, config: dict) -> RagContext:
        if not self.is_enabled(config):
            return ctx

        # 去重
        if config.get("dedup", True):
            seen = set()
            unique_sources = []
            for s in ctx.sources:
                kid = s.get("knowledge_id")
                if kid not in seen:
                    seen.add(kid)
                    unique_sources.append(s)
            ctx.sources = unique_sources

        # 上下文长度限制
        max_context = config.get("max_context_length", 8000)
        if len(ctx.answer) > max_context:
            ctx.answer = ctx.answer[:max_context] + "...(已截断)"

        return ctx


class StageRegistry:
    """阶段注册表"""
    _stages: dict[str, type[PipelineStage]] = {}
    _builtin_stages: list[type[PipelineStage]] = [
        QueryRewriteStage,
        WikiRetrievalStage,
        VectorSearchStage,
        RerankStage,
        LLMGenerateStage,
        PostProcessStage,
    ]

    @classmethod
    def register(cls, name: str, stage_cls: type[PipelineStage]):
        cls._stages[name] = stage_cls
        logger.info(f"Registered pipeline stage: {name}")

    @classmethod
    def get(cls, name: str) -> type[PipelineStage] | None:
        return cls._stages.get(name)

    @classmethod
    def get_all(cls) -> dict[str, type[PipelineStage]]:
        return cls._stages.copy()

    @classmethod
    def discover_from_config(cls, config: dict):
        """从配置发现自定义阶段"""
        for entry in config.get("custom_stages", []):
            module_path = entry.get("module")
            class_name = entry.get("class")
            name = entry.get("name", class_name.lower())
            if module_path and class_name:
                try:
                    import importlib
                    mod = importlib.import_module(module_path)
                    stage_cls = getattr(mod, class_name)
                    cls.register(name, stage_cls)
                except Exception as e:
                    logger.error(f"Failed to load custom stage {name}: {e}")

    @classmethod
    def init_builtins(cls):
        """初始化内置阶段"""
        for stage_cls in cls._builtin_stages:
            instance = stage_cls()
            cls.register(instance.name, stage_cls)


# 初始化内置阶段
StageRegistry.init_builtins()


class RagPipeline:
    """RAG 管线编排器"""

    def __init__(self, pipeline_config: list[dict] | None = None):
        self._stages: list[tuple[PipelineStage, dict]] = []
        self._build_from_config(pipeline_config or [])

    def _build_from_config(self, config: list[dict]):
        """从配置构建管线"""
        for entry in config:
            stage_name = entry.get("stage")
            stage_cls = StageRegistry.get(stage_name)
            if stage_cls:
                instance = stage_cls()
                self._stages.append((instance, entry))
            else:
                logger.warning(f"Unknown pipeline stage: {stage_name}")

    def add_stage(self, stage: PipelineStage, config: dict):
        """添加阶段"""
        self._stages.append((stage, config))

    async def execute(self, question: str, conversation_history: list[dict] = None,
                      **kwargs) -> dict:
        """执行管线"""
        ctx = RagContext(
            question=question,
            conversation_history=conversation_history or [],
            **kwargs,
        )

        for stage, config in self._stages:
            if stage.is_enabled(config):
                try:
                    ctx = await stage.execute(ctx, config)
                except Exception as e:
                    logger.error(f"Stage {stage.name} failed: {e}")

        return {
            "answer": ctx.answer,
            "sources": ctx.sources,
            "wiki_context": ctx.wiki_context,
        }


def create_pipeline_from_config() -> RagPipeline | None:
    """从配置创建管线"""
    if not Config.get("rag.pipeline.enabled", False):
        return None

    pipeline_config = Config.get("rag.pipeline.stages", [])
    pipeline = RagPipeline(pipeline_config)

    # 加载自定义阶段
    StageRegistry.discover_from_config(Config.get_all().get("rag", {}))

    return pipeline
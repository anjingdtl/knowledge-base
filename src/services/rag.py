"""RAG 检索增强生成管线 — 查询重写 → 混合检索 → 重排序 → LLM 生成"""
from concurrent.futures import ThreadPoolExecutor

from src.utils.config import Config
from src.services.query_rewriter import QueryRewriter
from src.services.hybrid_search import HybridSearcher
from src.services.reranker import LLMReranker
from src.services.llm import LLMService
from src.services.db import Database
from src.utils.llm_text import strip_think

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


class RAGService:
    def __init__(self):
        self.rewriter = QueryRewriter()
        self.searcher = HybridSearcher()
        self.reranker = LLMReranker()
        self.llm = LLMService()
        # 尝试加载可配置的 pipeline
        self._pipeline = None
        if Config.get("rag.pipeline.enabled", False):
            try:
                from src.services.rag_pipeline import create_pipeline_from_config
                self._pipeline = create_pipeline_from_config()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to load pipeline: {e}")

    def query(self, question: str, conversation_history: list[dict] | None = None,
              phase_callback=None) -> dict:
        # 如果配置了 pipeline.enabled=true，使用新管线
        if self._pipeline:
            import asyncio
            return asyncio.run(self._pipeline.execute(
                question,
                conversation_history=conversation_history,
            ))

        # 否则使用传统方式（后向兼容）
        top_k = Config.get("rag.top_k", 5)
        rerank_top_n = Config.get("rag.rerank.top_n", 5)
        score_threshold = Config.get("rag.score_threshold", 0.5)

        # Phase 1: 查询改写（与 Wiki 查询并发）
        if phase_callback:
            phase_callback("rewriting", "查询改写")
        with ThreadPoolExecutor(max_workers=2) as pool:
            rewrite_future = pool.submit(self.rewriter.rewrite, question)
            wiki_future = pool.submit(self._get_wiki_context, question)
            queries = rewrite_future.result()
            wiki_context = wiki_future.result()

        # Phase 2: 混合检索
        if phase_callback:
            phase_callback("searching", "混合检索")
        candidates = self.searcher.search(queries, top_k=top_k)

        # Phase 3: 重排序
        if phase_callback:
            phase_callback("reranking", "结果重排序")
        results = self.reranker.rerank(question, candidates, top_n=rerank_top_n)

        # Phase 4: 组装上下文 + LLM 生成
        if phase_callback:
            phase_callback("generating", "生成回答")

        if results and "rerank_score" in results[0]:
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

        answer = strip_think(self.llm.chat(messages))
        return {"answer": answer, "sources": sources}

    def query_stream(self, question: str, conversation_history: list[dict] | None = None,
                     phase_callback=None):
        top_k = Config.get("rag.top_k", 5)
        rerank_top_n = Config.get("rag.rerank.top_n", 5)
        score_threshold = Config.get("rag.score_threshold", 0.5)

        # Phase 1: 查询改写（与 Wiki 查询并发）
        if phase_callback:
            phase_callback("rewriting", "查询改写")
        with ThreadPoolExecutor(max_workers=2) as pool:
            rewrite_future = pool.submit(self.rewriter.rewrite, question)
            wiki_future = pool.submit(self._get_wiki_context, question)
            queries = rewrite_future.result()
            wiki_context = wiki_future.result()

        # Phase 2: 混合检索
        if phase_callback:
            phase_callback("searching", "混合检索")
        candidates = self.searcher.search(queries, top_k=top_k)

        # Phase 3: 重排序
        if phase_callback:
            phase_callback("reranking", "结果重排序")
        results = self.reranker.rerank(question, candidates, top_n=rerank_top_n)

        # Phase 4: 组装上下文 + LLM 流式生成
        if phase_callback:
            phase_callback("generating", "生成回答")

        if results and "rerank_score" in results[0]:
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

        return self.llm.chat_stream(messages, silent=True), sources

    def _get_wiki_context(self, query: str) -> str:
        """从 Wiki 层检索结构化知识作为附加上下文"""
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
        """批量查询标题，组装上下文和来源列表"""
        kid_map = {}
        for r in filtered:
            kid = r["metadata"].get("knowledge_id", "")
            if kid:
                kid_map[kid] = True

        items = Database.get_knowledge_batch(list(kid_map.keys())) if kid_map else {}

        context_parts = []
        sources = []
        for i, result in enumerate(filtered):
            context_parts.append(f"[来源{i+1}]\n{result['text']}")
            kid = result["metadata"].get("knowledge_id", "")
            item = items.get(kid)
            title = result["metadata"].get("title") or (item["title"] if item else "未知")
            sources.append({
                "chunk_id": result.get("id", ""),
                "knowledge_id": kid,
                "title": title,
                "text_preview": result["text"][:200],
                "score": result.get("rerank_score", result.get("distance", 0)),
            })
        return context_parts, sources

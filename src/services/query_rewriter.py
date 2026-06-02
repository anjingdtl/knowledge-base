"""查询重写模块 — 利用 LLM 生成多个改写查询以提升召回率"""
import re
from src.utils.config import Config
from src.services.llm import LLMService
from src.utils.llm_text import strip_think


class QueryRewriter:
    def __init__(self, llm=None, config=None):
        self.llm = llm or LLMService()
        self._config = config or Config

    def rewrite(self, query: str, num_variations: int = 3) -> list[str]:
        """返回 [原始query, 改写query1, 改写query2, ...]"""
        if not self._config.get("rag.enable_query_rewriting", False):
            return [query]

        prompt = (
            "你是一个查询改写专家。请将用户的原始问题改写为 2 个不同表达方式的搜索查询，"
            "以便从知识库中检索到更全面的相关内容。\n\n"
            f"原始问题：{query}\n\n"
            "请直接输出改写后的查询，每行一个，不要编号，不要解释。"
        )
        try:
            response = self.llm.chat([{"role": "user", "content": prompt}], silent=True)
            cleaned = strip_think(response)
            lines = [line.strip() for line in cleaned.split("\n") if line.strip()]
            # 过滤掉纯英文或过长的非查询行
            queries = []
            for line in lines:
                if len(line) > 100:
                    continue
                if re.search(r'[一-鿿]', line):
                    queries.append(line)
                elif len(line) < 50:
                    queries.append(line)
            return [query] + queries[:num_variations]
        except Exception:
            return [query]

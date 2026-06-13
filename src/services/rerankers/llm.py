"""LLM 回退重排序器 — 使用 LLM 打分作为重排序方案"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.llm import LLMService
    from src.utils.config import Config

logger = logging.getLogger(__name__)


class LLMFallbackReranker:
    """Reranker using LLM scoring as fallback when dedicated rerank API unavailable."""

    def __init__(
        self,
        llm: "LLMService | None" = None,
        config: "Config | None" = None,
    ):
        self._llm = llm
        self._config = config

    def rerank(self, query: str, candidates: list[dict], top_n: int = 5) -> list[dict]:
        """Use LLM to score relevance. Same logic as the legacy LLMReranker._score_batch_llm."""
        if not candidates:
            return []

        min_score = 0.3
        if self._config is not None:
            min_score = self._config.get("rag.rerank.min_score", 0.3)

        try:
            scores = self._score_batch_llm(query, candidates)
        except Exception as e:
            logger.warning("LLM fallback reranker failed: %s", e)
            return candidates

        # 应用分数
        for i, cand in enumerate(candidates):
            cand["rerank_score"] = scores[i] if i < len(scores) else 0.5

        # 排序并过滤
        candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        filtered = [s for s in candidates if s.get("rerank_score", 0) >= min_score][:top_n]

        # 重排序器过滤太严时，保留 top_n 结果避免上下文为空
        if not filtered and candidates:
            filtered = candidates[:top_n]

        return filtered

    def _score_batch_llm(self, query: str, candidates: list[dict]) -> list[float]:
        """使用 LLM 批量打分（回退方案）"""
        from src.utils.llm_text import strip_think

        if self._llm is None:
            logger.warning("LLM fallback reranker: no LLM service provided")
            return [0.5] * len(candidates)

        docs_text = ""
        for i, cand in enumerate(candidates):
            docs_text += f"\n[{i}] {cand.get('text', '')[:300]}\n"

        prompt = (
            "请评估以下每个文档片段与用户问题的相关程度。\n"
            "对每个文档输出一个 0 到 1 之间的数字（1=完全相关，0=完全不相关）。\n"
            "严格按以下格式输出，每行一个，不要输出其他内容：\n"
            "0:分数\n1:分数\n2:分数\n...\n\n"
            f"用户问题：{query}\n"
            f"文档列表：{docs_text}"
        )
        try:
            response = self._llm.chat([{"role": "user", "content": prompt}], silent=True)
            cleaned = strip_think(response)
            return self._parse_scores(cleaned, len(candidates))
        except Exception:
            return [0.5] * len(candidates)

    @staticmethod
    def _parse_scores(response: str, count: int) -> list[float]:
        """解析 LLM 返回的分数数字"""
        scores = []
        for line in response.strip().split("\n"):
            line = line.strip()
            for part in line.replace("：", ":").split(","):
                part = part.strip()
                if ":" in part:
                    part = part.split(":", 1)[1].strip()
                try:
                    val = float(part)
                    if 0 <= val <= 1:
                        scores.append(val)
                except ValueError:
                    continue
                if len(scores) >= count:
                    break
            if len(scores) >= count:
                break
        while len(scores) < count:
            scores.append(0.5)
        return scores[:count]

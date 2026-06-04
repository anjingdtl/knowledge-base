"""重排序模块 — 支持专用重排序模型或 LLM 打分回退"""
import logging
from typing import Optional

from src.utils.config import Config
from src.services.llm import LLMService
from src.utils.llm_text import strip_think

logger = logging.getLogger(__name__)


class LLMReranker:
    def __init__(self, llm=None, config=None):
        self._llm = llm or LLMService()
        self._config = config or Config
        self._enabled = self._config.get("reranker.enabled", True)
        self._use_llm_fallback = self._config.get("reranker.use_llm_fallback", True)

    def rerank(self, query: str, candidates: list[dict], top_n: int = 5) -> list[dict]:
        """对候选结果重排，返回 top_n 结果"""
        if not self._enabled:
            return candidates[:top_n]

        if not candidates:
            return []

        min_score = self._config.get("rag.rerank.min_score", 0.3)

        # 优先使用专用重排序模型
        reranker_model = self._config.get("reranker.model", "")
        if reranker_model:
            try:
                scores = self._rerank_with_model(query, candidates, reranker_model)
            except Exception as e:
                logger.warning(f"Rerank model failed: {e}, falling back to LLM")
                if self._use_llm_fallback:
                    scores = self._score_batch_llm(query, candidates)
                else:
                    return candidates[:top_n]
        else:
            # 使用 LLM 打分
            scores = self._score_batch_llm(query, candidates)

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

    def _rerank_with_model(self, query: str, candidates: list[dict], model: str) -> list[float]:
        """使用专用重排序模型 API"""
        import httpx

        # 获取重排序模型配置
        base_url = self._config.get("reranker.base_url", "")
        api_key = self._config.get("reranker.api_key", "")

        if not base_url or not api_key:
            # 尝试复用 embedding 配置
            base_url = self._config.get("embedding.base_url", "")
            api_key = self._config.get("embedding.api_key", "")

        if not base_url or not api_key:
            raise ValueError("No API key or base URL configured for reranker")

        # 准备文档文本
        texts = [cand.get("text", "")[:1000] for cand in candidates]

        # 调用重排序 API
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": model,
                "query": query,
                "documents": texts,
                "top_n": min(len(texts), 10),
            }

            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    f"{base_url}/rerank",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json()

            scores = []
            for item in result.get("results", []):
                scores.append(item.get("relevance_score", 0.5))
            return scores

        except Exception as e:
            logger.warning(f"Rerank API call failed: {e}")
            raise

    def _score_batch_llm(self, query: str, candidates: list[dict]) -> list[float]:
        """使用 LLM 批量打分（回退方案）"""
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


def get_reranker() -> LLMReranker:
    """获取重排序器实例（单例）"""
    if not hasattr(get_reranker, "_instance"):
        get_reranker._instance = LLMReranker()
    return get_reranker._instance

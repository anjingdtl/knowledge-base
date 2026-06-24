"""三级行星齿轮路由引擎 — Rule → Embedding → LLM 逐级降级

Phase 2 / search-optimize: 替代 AgenticRouter 的 LLM-heavy 路由策略。

Level 1 (太阳轮) RuleRouter:    0ms — 正则 + 已知标签匹配，覆盖 70% + 结构化查询
Level 2 (行星轮) EmbeddingRouter: <100ms — tag/title embedding 相似度，覆盖 20% 边界case
Level 3 (齿圈)   LLMRouter:     5s timeout — LLM 分类兜底，覆盖 10% 复杂语义

任何一级返回结果即终止，不向下一级传递。
"""
import json
import logging
import re
from typing import Any

from src.models.query_dsl import QuerySpec
from src.services.db import Database
from src.utils.config import Config

logger = logging.getLogger(__name__)

# ── 共享常量（从 agentic_router.py 迁移） ──

_LOGIC_SIGNALS = (
    "所有", "全部", "列出", "找出", "查找", "筛选", "过滤",
    "状态", "属于", "包含", "不包含", "不是",
    "哪些", "多少", "统计",
    "find all", "list all", "show all", "filter", "where",
)

_STRUCTURED_STRONG_SIGNALS = (
    "所有", "全部", "列出", "找出", "查找", "筛选", "过滤", "统计",
    "find all", "list all", "show all", "filter", "where",
)

_GRAPH_SIGNALS = (
    "关系", "链接", "引用", "关联", "图谱",
    "related to", "links to", "references", "graph",
)

_NL_TAG_RE = re.compile(
    r"(?:标记为|标签为|tagged?|tag)\s+([\w\u4e00-\u9fff-]+)", re.IGNORECASE
)

_NL_PROP_RE = re.compile(
    r"(状态|优先级|类型|标题|版本|status|priority|type|title|version)"
    r"\s*(?:为|是|等于|is|eq|=)\s*"
    r"([\w\u4e00-\u9fff-]+)",
    re.IGNORECASE,
)

_NL_TITLE_RE = re.compile(
    r"(?:标题|title)\s*(?:为|是|等于|is|eq|=)\s*"
    r"(.+?)(?:\s+的(?:知识|条目|文档)?|$)",
    re.IGNORECASE,
)

_NL_LINK_RE = re.compile(
    r"(?:与|和|to)\s+(.+?)\s+(?:页面|page|相关|linked|链接)", re.IGNORECASE
)

_TAG_KEYWORDS = {"标记", "标签", "tag", "tagged"}

_PROP_NAME_MAP = {
    "状态": "status",
    "优先级": "priority",
    "类型": "type",
    "标题": "title",
    "版本": "version",
}


class RuleRouter:
    """Level 1: 基于正则 + 已知标签的零延迟路由"""

    def __init__(self, db=None):
        self._db = db or Database

    def route(self, question: str) -> dict | None:
        """返回路由结果或 None（无法判断时交给下一级）

        L1 只返回正则匹配出的精确条件，强信号但没有精确匹配时
        交给 L2/L3 去获取更精确的 QuerySpec，而非直接 fulltext。
        """
        # 1. 正则匹配结构化条件
        rule_spec = self._try_rule_based(question)
        if rule_spec is not None:
            return {"mode": "structured", "query_spec": rule_spec,
                    "explanation": "rule-based routing (L1)"}

        # 2. 图谱信号检测
        if self._is_graph_query(question):
            return None  # graph 路由交给 LLM 级判断

        # 3. 强信号词 → 不直接返回 fulltext，交给 L2/L3
        #    强信号只表示"这不是纯语义查询"，但不代表 L1 能构建精确 spec
        return None

    def _is_structured(self, question: str) -> bool:
        lower = question.lower()
        return any(signal in lower for signal in _STRUCTURED_STRONG_SIGNALS)

    def _is_graph_query(self, question: str) -> bool:
        lower = question.lower()
        return any(signal in lower for signal in _GRAPH_SIGNALS)

    def _try_rule_based(self, question: str) -> "QuerySpec | None":
        conditions: list[dict[str, Any]] = []
        has_title_condition = False

        for m in _NL_TITLE_RE.finditer(question):
            title = m.group(1).strip()
            if title:
                conditions.append({"title": {"contains": title}})
                has_title_condition = True

        for m in _NL_TAG_RE.finditer(question):
            conditions.append({"tag": m.group(1)})

        for tag in self._known_tags_in_question(question):
            entry: dict[str, Any] = {"tag": tag}
            if entry not in conditions:
                conditions.append(entry)

        for m in _NL_PROP_RE.finditer(question):
            key = m.group(1)
            value = m.group(2)
            if key.lower() in _TAG_KEYWORDS:
                continue
            key = _PROP_NAME_MAP.get(key, key)
            if key == "title":
                if has_title_condition:
                    continue
                entry = {"title": {"contains": value}}
                has_title_condition = True
            else:
                entry = {"property": {"key": key, "op": "eq", "value": value}}
            if entry not in conditions:
                conditions.append(entry)

        for m in _NL_LINK_RE.finditer(question):
            title = m.group(1).strip()
            conditions.append({"link": f"[[{title}]]"})

        # Legacy QueryRouter integration
        try:
            from src.services.query_router import QueryRouter
            legacy = QueryRouter(db=self._db).route(question)
            if legacy.mode == "logic":
                for tag in legacy.tags:
                    entry: dict[str, Any] = {"tag": tag}
                    if entry not in conditions:
                        conditions.append(entry)
                for key, value in legacy.properties.items():
                    entry = {"property": {"key": key, "op": "eq", "value": value}}
                    if entry not in conditions:
                        conditions.append(entry)
                for title in legacy.link_titles:
                    entry = {"link": f"[[{title}]]"}
                    if entry not in conditions:
                        conditions.append(entry)
        except Exception:
            pass

        if not conditions:
            return None
        if len(conditions) == 1:
            filter_data = conditions[0]
        else:
            filter_data = {"and": conditions}
        return QuerySpec.from_json({"filter": filter_data})

    def _known_tags_in_question(self, question: str) -> list[str]:
        if not question:
            return []
        try:
            tags = self._db.get_all_tags()
        except Exception:
            return []
        matches = []
        for tag in sorted(tags, key=len, reverse=True):
            if len(tag) < 2:
                continue
            if tag in question and tag not in matches:
                matches.append(tag)
        return matches


class EmbeddingRouter:
    """Level 2: 基于 tag/title embedding 相似度的低延迟路由

    对 query 做 embedding，与已知 tag embedding 比对，
    如果最高相似度超过阈值则路由为 structured + tag filter。
    """

    def __init__(self, db=None, similarity_threshold: float = 0.75):
        self._db = db or Database
        self._similarity_threshold = similarity_threshold
        self._tag_embeddings_cache: dict[str, list[float]] | None = None

    def route(self, question: str) -> dict | None:
        """返回路由结果或 None（无法判断时交给下一级）"""
        try:
            from src.services.embedding import EmbeddingService
            emb_service = EmbeddingService()
            query_emb = emb_service.embed(question)
            if not query_emb:
                return None

            tag_embs = self._get_tag_embeddings(emb_service)
            if not tag_embs:
                return None

            best_tag = None
            best_sim = 0.0
            for tag, tag_emb in tag_embs.items():
                sim = self._cosine_sim(query_emb, tag_emb)
                if sim > best_sim:
                    best_sim = sim
                    best_tag = tag

            if best_sim >= self._similarity_threshold and best_tag:
                logger.debug(f"EmbeddingRouter: best_tag={best_tag}, sim={best_sim:.3f}")
                spec = QuerySpec.from_json({"filter": {"tag": best_tag}})
                return {"mode": "structured", "query_spec": spec,
                        "explanation": f"embedding routing (L2, tag={best_tag}, sim={best_sim:.2f})"}
        except Exception as e:
            logger.debug(f"EmbeddingRouter failed: {e}")
        return None

    def _get_tag_embeddings(self, emb_service) -> dict[str, list[float]]:
        if self._tag_embeddings_cache is not None:
            return self._tag_embeddings_cache
        try:
            tags = self._db.get_all_tags()
        except Exception:
            return {}
        tag_embs = {}
        # 只 embed 长度 >= 2 的 tag，限制最多 200 个避免太慢
        for tag in sorted(tags)[:200]:
            if len(tag) < 2:
                continue
            try:
                emb = emb_service.embed(tag)
                if emb:
                    tag_embs[tag] = emb
            except Exception:
                continue
        self._tag_embeddings_cache = tag_embs
        return tag_embs

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


_SYSTEM_PROMPT = """You are a query translator. Convert the user's natural language question into a JSON query DSL.

The DSL supports these filter types:
- {"tag": "tag_name"} — filter by tag
- {"property": {"key": "prop_name", "op": "eq|ne|gt|gte|lt|lte|in|contains|like", "value": ...}} — filter by property
- {"fulltext": "search text"} — full-text search
- {"title": {"contains": "title text"}} — filter by knowledge title
- {"link": "[[Page Title]]"} — filter by link to page
- {"file_type": "md"} — filter by file type
- {"source_type": "manual"} — filter by source type
- {"and": [...]} — AND group
- {"or": [...]} — OR group
- {"not": {...}} — NOT condition

Additional fields: "limit" (int), "offset" (int), "sort" ({"by": "field", "order": "asc|desc"})

If the question is a fuzzy/semantic question that cannot be expressed as structured filters, respond with:
{"mode": "hybrid", "query": "original question"}

If the question can be expressed as structured filters, respond with:
{"mode": "structured", "query": {...DSL JSON...}}

If the question asks about relationships/links between pages, respond with:
{"mode": "graph", "query": {...DSL JSON...}, "traverse": {"start_type": "knowledge", "max_depth": 2}}

Respond with ONLY valid JSON, no markdown or explanation."""


class LLMRouter:
    """Level 3: LLM 分类兜底（5s timeout）"""

    def __init__(self, llm=None, timeout: float = 5.0):
        self._llm = llm
        self._timeout = timeout

    def route(self, question: str) -> dict | None:
        """返回路由结果或 None（LLM 不可用/超时/解析失败）"""
        llm = self._resolve_llm()
        if llm is None:
            return None
        try:
            import threading
            result: dict | None = None
            exc: Exception | None = None

            def _call():
                nonlocal result, exc
                try:
                    response = llm.chat(
                        messages=[
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": question},
                        ],
                    )
                    text = response.strip()
                    if text.startswith("```"):
                        text = re.sub(r"^```\w*\n?", "", text)
                        text = re.sub(r"\n?```$", "", text)
                    parsed = json.loads(text)
                    if parsed.get("mode") == "hybrid":
                        result = {"mode": "hybrid", "query_spec": None, "explanation": "LLM classified as hybrid (L3)"}
                    elif "query" in parsed:
                        spec = QuerySpec.from_json(parsed["query"])
                        result = {"mode": parsed.get("mode", "structured"), "query_spec": spec,
                                  "explanation": "LLM routing (L3)"}
                        if "traverse" in parsed:
                            result["traverse"] = parsed["traverse"]
                except Exception as e:
                    exc = e

            t = threading.Thread(target=_call, daemon=True)
            t.start()
            t.join(timeout=self._timeout)
            if t.is_alive():
                logger.debug(f"LLMRouter timed out after {self._timeout}s")
                return None
            if exc is not None:
                logger.debug(f"LLMRouter failed: {exc}")
                return None
            return result
        except Exception as e:
            logger.debug(f"LLMRouter error: {e}")
            return None

    def _resolve_llm(self):
        if self._llm is not None:
            return self._llm
        try:
            from src.core.container import get_active_container
            container = get_active_container()
            if container is not None and container.llm is not None:
                self._llm = container.llm
                return self._llm
        except Exception:
            pass
        return None


class PlanetaryRouter:
    """三级行星齿轮路由入口

    执行顺序: RuleRouter → EmbeddingRouter → LLMRouter
    任何一级返回结果即终止，否则默认 hybrid。
    """

    def __init__(self, db=None, llm=None):
        self._db = db or Database
        self._rule_router = RuleRouter(db=self._db)
        self._embedding_router = EmbeddingRouter(db=self._db)
        self._llm_timeout = float(Config.get("rag.route_llm_timeout", 5))
        self._llm_router = LLMRouter(llm=llm, timeout=self._llm_timeout)

    def route(self, question: str) -> dict:
        """三级路由：L1 → L2 → L3，任何一级成功即返回"""

        # Level 1: RuleRouter (0ms)
        result = self._rule_router.route(question)
        if result is not None:
            logger.debug(f"PlanetaryRouter: L1 rule resolved → {result.get('mode')}")
            return result

        # Level 1.5: Graph 信号检测 + LLM 或 structured 回退
        if self._rule_router._is_graph_query(question):
            llm_result = self._llm_router.route(question)
            if llm_result is not None:
                if llm_result.get("mode") == "hybrid":
                    # LLM 主动判定为 hybrid（确保 query_spec key 存在）
                    llm_result.setdefault("query_spec", None)
                    return llm_result
                # LLM 返回了 structured/graph
                return llm_result
            # LLM 不可用：graph 查询回退到 structured fulltext
            logging.warning("LLM unavailable for graph routing, falling back to fulltext structured")
            return {"mode": "structured", "query_spec": QuerySpec.from_json(
                {"filter": {"fulltext": question}}
            ), "explanation": "graph signal detected, fallback to structured (LLM unavailable)"}

        # Level 2: EmbeddingRouter (<100ms)
        result = self._embedding_router.route(question)
        if result is not None:
            logger.debug(f"PlanetaryRouter: L2 embedding resolved → {result.get('mode')}")
            return result

        # Level 3: LLMRouter (5s timeout)
        result = self._llm_router.route(question)
        if result is not None:
            # 确保 hybrid 结果也包含 query_spec key
            result.setdefault("query_spec", None)
            logger.debug(f"PlanetaryRouter: L3 LLM resolved → {result.get('mode')}")
            return result

        # 兜底: 如果有强信号词（"所有/全部/统计"等），走 structured fulltext
        # 否则走 hybrid（强信号但没有精确匹配时也走 hybrid，比 fulltext 更宽容）
        if self._rule_router._is_structured(question):
            logger.debug("PlanetaryRouter: all levels failed, strong signal detected → structured fulltext")
            return {"mode": "structured", "query_spec": QuerySpec.from_json(
                {"filter": {"fulltext": question}}
            ), "explanation": "rule-based structured (L1 strong signal, L2/L3 failed)"}

        logger.debug("PlanetaryRouter: all levels failed, fallback to hybrid")
        return {"mode": "hybrid", "query_spec": None,
                "explanation": "fallback to hybrid search (all router levels failed)"}

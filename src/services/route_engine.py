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
import threading
import time
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

# 英文标签语法需冒号（tagged: X / tag: X），避免 "tagged with python"
# 把介词 with 误当 tag；中文 "标记为/标签为 X" 保持原义。value 组用 [\w-]
# （Python re 默认 Unicode 下 \w 已含中文，等价旧 [\w\u4e00-\u9fff-]）。
_NL_TAG_RE = re.compile(
    r"(?:标记为|标签为|(?:tagged|tag)[:：])\s*([\w-]+)", re.IGNORECASE
)

# value 用非贪婪 + lookahead：遇到连词(并且/且/和/与)或下一个「属性+分隔符」
# 或空白/行尾/非值字符即停——既避免多条件被贪婪吞掉，也允许 value 在空格处
# 自然结束（"状态为 open 的文档" 不再把 open 丢掉）。字符组补 . / - 容纳版本号等。
_NL_PROP_RE = re.compile(
    r"(状态|优先级|类型|标题|版本|status|priority|type|title|version)"
    r"\s*(?:为|是|等于|is|eq|=)\s*"
    r"([\w./-]+?)"
    r"(?=\s*(?:并且|且|和|与|,|，)"
    r"|(?:状态|优先级|类型|标题|版本|status|priority|type|title|version)"
    r"\s*(?:为|是|等于|is|eq|=)"
    r"|\s|$|[^\w./-])",
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
                    entry = {"tag": tag}
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

    BUG-1 fix (50轮测试报告): 标签覆盖率仅 3.7% 时 tag 匹配几乎必然落空，
    导致 100% fallback 到 hybrid。新增 title embedding 兜底——当 tag 匹配
    失败时，用文档标题 embedding 做二次匹配，命中高相似度标题则路由为
    structured + title contains filter，避免路由功能完全退化。
    """

    # 模块级 tag embedding 缓存（类属性，跨实例共享）：[timestamp, {tag: emb}]。
    # 旧实现是实例级、首次填充后整个实例生命周期不更新；而 AgenticRouter/
    # PlanetaryRouter 每次请求都 new 一个 EmbeddingRouter，导致每次 L2 路由都
    # 重新 embed 全部 tag（≤200 次 API 调用）。改为类级缓存 + TTL，跨请求复用、
    # TTL 到期自动重建（新增/删除 tag 后最迟 TTL 秒内生效）。
    _TAG_EMB_CACHE: tuple[float, dict[str, list[float]]] | None = None
    _TAG_EMB_LOCK = threading.Lock()
    # title embedding 缓存：(timestamp, [(title, emb), ...])
    _TITLE_EMB_CACHE: tuple[float, list[tuple[str, list[float]]]] | None = None
    _TITLE_EMB_LOCK = threading.Lock()

    def __init__(self, db=None, similarity_threshold: float = 0.60,
                 title_similarity_threshold: float = 0.70):
        self._db = db or Database
        # BUG-1 fix: 降低阈值 0.75→0.60，提高冷启动场景下 tag embedding 匹配率
        # 标签覆盖率 3.7% 时较高的 0.75 几乎不可能命中，0.60 在不引入明显噪声的前提下提升路由可用性
        self._similarity_threshold = similarity_threshold
        # title 匹配阈值略高于 tag：标题语义更具体，要求更高相似度避免误命中
        self._title_similarity_threshold = title_similarity_threshold

    def route(self, question: str) -> dict | None:
        """返回路由结果或 None（无法判断时交给下一级）

        两级匹配：tag embedding → title embedding。
        tag 命中即返回（更精准）；tag 落空时尝试 title 兜底。
        """
        try:
            from src.services.embedding import EmbeddingService
            emb_service = EmbeddingService()
            query_emb = emb_service.embed(question)
            if not query_emb:
                return None

            # ── 第一级：tag embedding 匹配 ──
            tag_embs = self._get_tag_embeddings(emb_service)
            if tag_embs:
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

            # ── 第二级：title embedding 兜底（标签覆盖率不足时关键） ──
            # 50轮测试报告 Bug-1: 标签覆盖率 3.7% 时 tag 匹配几乎必然落空，
            # 用标题语义匹配作为兜底，命中高相似度标题则路由为 title contains，
            # 避免 100% fallback 到 hybrid。
            title_embs = self._get_title_embeddings(emb_service)
            if title_embs:
                best_title = None
                best_title_sim = 0.0
                for title, title_emb in title_embs:
                    sim = self._cosine_sim(query_emb, title_emb)
                    if sim > best_title_sim:
                        best_title_sim = sim
                        best_title = title

                if best_title_sim >= self._title_similarity_threshold and best_title:
                    logger.debug(
                        f"EmbeddingRouter: title fallback best_title={best_title!r}, sim={best_title_sim:.3f}"
                    )
                    spec = QuerySpec.from_json(
                        {"filter": {"title": {"contains": best_title}}}
                    )
                    return {
                        "mode": "structured",
                        "query_spec": spec,
                        "explanation": (
                            f"embedding routing (L2 title-fallback, "
                            f"title={best_title}, sim={best_title_sim:.2f})"
                        ),
                    }
        except Exception as e:
            logger.debug(f"EmbeddingRouter failed: {e}")
        return None

    def _get_tag_embeddings(self, emb_service) -> dict[str, list[float]]:
        ttl = float(Config.get("rag.tag_embedding_cache_ttl", 300) or 300)
        # 快速路径：缓存命中且未过期则直接返回（仅读，不重建）
        cache = EmbeddingRouter._TAG_EMB_CACHE
        if cache is not None and (time.monotonic() - cache[0]) < ttl:
            return cache[1]
        # 未命中或过期：加锁重建（重建期间其它并发请求等锁，避免重复 embed）
        with EmbeddingRouter._TAG_EMB_LOCK:
            cache = EmbeddingRouter._TAG_EMB_CACHE
            if cache is not None and (time.monotonic() - cache[0]) < ttl:
                return cache[1]
            try:
                tags = self._db.get_all_tags()
            except Exception:
                return cache[1] if cache is not None else {}
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
            EmbeddingRouter._TAG_EMB_CACHE = (time.monotonic(), tag_embs)
            return tag_embs

    def _get_title_embeddings(self, emb_service) -> list[tuple[str, list[float]]]:
        """获取文档标题 embedding 列表（带 TTL 缓存 + batch embed）。

        标签覆盖率不足时作为 L2 兜底依据。使用 embed_batch 批量请求，
        避免逐条调用产生大量 API 往返。
        """
        ttl = float(Config.get("rag.title_embedding_cache_ttl", 600) or 600)
        cache = EmbeddingRouter._TITLE_EMB_CACHE
        if cache is not None and (time.monotonic() - cache[0]) < ttl:
            return cache[1]
        with EmbeddingRouter._TITLE_EMB_LOCK:
            cache = EmbeddingRouter._TITLE_EMB_CACHE
            if cache is not None and (time.monotonic() - cache[0]) < ttl:
                return cache[1]
            try:
                rows = self._db.get_conn().execute(
                    "SELECT DISTINCT title FROM knowledge_items "
                    "WHERE deleted_at IS NULL AND title IS NOT NULL AND title != '' "
                    "ORDER BY title LIMIT 500"
                ).fetchall()
            except Exception:
                return cache[1] if cache is not None else []
            titles = [row["title"] for row in rows if row["title"]]
            if not titles:
                EmbeddingRouter._TITLE_EMB_CACHE = (time.monotonic(), [])
                return []
            try:
                embs = emb_service.embed_batch(titles, batch_size=32)
            except Exception as e:
                logger.debug(f"EmbeddingRouter title embed_batch failed: {e}")
                return cache[1] if cache is not None else []
            title_embs = [
                (title, emb)
                for title, emb in zip(titles, embs)
                if emb
            ]
            EmbeddingRouter._TITLE_EMB_CACHE = (time.monotonic(), title_embs)
            return title_embs

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot: float = sum(x * y for x, y in zip(a, b))
        norm_a: float = sum(x * x for x in a) ** 0.5
        norm_b: float = sum(x * x for x in b) ** 0.5
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
        """返回路由结果或 None（LLM 不可用/超时/解析失败）

        超时通过 llm.chat(timeout=self._timeout) 在单次 HTTP 请求层强制——
        超时点 httpx 抛异常、本同步调用自然返回，无需 threading。旧实现用
        daemon 线程 + join(timeout)，超时后线程仍阻塞在 llm.chat() 长达
        client timeout(默认 60s)，在 MCP 长生命周期进程里持续泄漏。
        """
        llm = self._resolve_llm()
        if llm is None:
            return None
        try:
            response = llm.chat(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
                timeout=self._timeout,
            )
            text = (response or "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            parsed = json.loads(text)
            if parsed.get("mode") == "hybrid":
                # BUG-1 fix: LLM hybrid 结果也附带 fulltext query_spec，保持与兜底逻辑一致
                return {"mode": "hybrid", "query_spec": QuerySpec.from_json(
                    {"filter": {"fulltext": question}}
                ), "explanation": "LLM classified as hybrid (L3), with fulltext query_spec"}
            if "query" in parsed:
                spec = QuerySpec.from_json(parsed["query"])
                result = {"mode": parsed.get("mode", "structured"), "query_spec": spec,
                          "explanation": "LLM routing (L3)"}
                if "traverse" in parsed:
                    result["traverse"] = parsed["traverse"]
                return result
        except Exception as e:
            logger.debug(f"LLMRouter failed: {e}")
        return None

    def _resolve_llm(self):
        # LLM must be constructor-injected (PlanetaryRouter passes llm through).
        return self._llm


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
        result: dict | None = self._rule_router.route(question)
        if result is not None:
            logger.debug(f"PlanetaryRouter: L1 rule resolved → {result.get('mode')}")
            return result

        # Level 1.5: Graph 信号检测 + LLM 或 structured 回退
        if self._rule_router._is_graph_query(question):
            llm_result: dict | None = self._llm_router.route(question)
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
            # BUG-1 fix: 确保 L3 返回的 hybrid 结果附带 fulltext query_spec（兜底）
            if result.get("mode") == "hybrid" and result.get("query_spec") is None:
                result["query_spec"] = QuerySpec.from_json({"filter": {"fulltext": question}})
                result["explanation"] = (result.get("explanation", "") + " (fulltext query_spec fallback)").strip()
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

        logger.debug("PlanetaryRouter: all levels failed, fallback to hybrid with fulltext query_spec")
        # BUG-1 fix: hybrid 兜底时附带 fulltext query_spec，确保调用方始终可获得可执行的查询规格
        # 不再返回 query_spec=None，Agent 可直接使用而无需二次构造
        return {"mode": "hybrid", "query_spec": QuerySpec.from_json(
            {"filter": {"fulltext": question}}
        ), "explanation": "fallback to hybrid search with fulltext query_spec (all router levels failed)"}

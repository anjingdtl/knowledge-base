import json
import logging
import re
from typing import Any

from src.models.query_dsl import QuerySpec
from src.services.db import Database
from src.utils.config import Config


def serialize_route(routing: dict) -> dict:
    """把 AgenticRouter.route() 的结果转成 JSON-safe dict（QuerySpec → dict）。

    Agent 客户端只能消费基本类型，所以 QuerySpec 必须序列化为 to_json()。
    """
    result = dict(routing)
    spec = result.get("query_spec")
    if spec is not None and hasattr(spec, "to_json"):
        result["query_spec"] = spec.to_json()
    elif spec is not None and not isinstance(spec, dict):
        result["query_spec"] = dict(spec)
    return result

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

_LOGIC_SIGNALS = (
    "所有", "全部", "列出", "找出", "查找", "筛选", "过滤",
    "状态", "属于", "包含", "不包含", "不是",
    "哪些", "多少", "统计",
    "find all", "list all", "show all", "filter", "where",
)

# _is_structured 兜底用的强信号子集：只保留明确表达"列举/筛选"意图的词，
# 去掉"状态/包含/哪些/属于"等易在日常语义查询中误命中的弱信号词。
# 否则"Python 异步编程有哪些最佳实践"这类纯语义查询会被误判为 structured。
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


class AgenticRouter:
    def __init__(self, db=None, llm=None):
        self._db = db or Database
        self._llm = llm
        self._planetary_router = None  # 缓存实例，避免每次调用重建

    def route(self, question: str) -> dict:
        # Phase 2: 行星齿轮路由开关
        use_planetary = Config.get("rag.use_planetary_router", True)
        if use_planetary:
            if self._planetary_router is None:
                from src.services.route_engine import PlanetaryRouter
                self._planetary_router = PlanetaryRouter(db=self._db, llm=self._llm)
            return self._planetary_router.route(question)

        # Legacy routing path (below)
        rule_spec = self._try_rule_based(question)
        if rule_spec is not None:
            return {"mode": "structured", "query_spec": rule_spec, "explanation": "rule-based routing"}

        if self._is_graph_query(question):
            llm_route = self._try_llm(question)
            if llm_route is not None:
                if llm_route.get("mode") == "hybrid":
                    # LLM 主动判定为 hybrid
                    return {"mode": "hybrid", "query_spec": None,
                            "explanation": "LLM classified as hybrid search"}
                return {"mode": "graph", "query_spec": llm_route.get("query_spec"),
                        "traverse": llm_route.get("traverse", {"max_depth": 2}),
                        "explanation": "LLM graph routing"}
            # LLM 不可用时的 graph 回退：构建 fulltext QuerySpec，走 structured 执行器。
            # mode 与 query_spec 必须语义一致 —— hybrid 执行器会忽略 query_spec，
            # 导致 fulltext filter 丢失，故这里用 structured（与下方 _is_structured 兜底一致）。
            logging.warning("LLM unavailable for graph routing, falling back to fulltext structured")
            from src.models.query_dsl import QuerySpec
            return {"mode": "structured", "query_spec": QuerySpec.from_json(
                {"filter": {"fulltext": question}}
            ), "explanation": "fallback to structured (LLM unavailable, graph signal)"}

        llm_route = self._try_llm(question)
        if llm_route is not None:
            if llm_route.get("mode") == "structured":
                return {"mode": "structured", "query_spec": llm_route["query_spec"],
                        "explanation": "LLM structured routing"}
            if llm_route.get("mode") == "hybrid":
                return {"mode": "hybrid", "query_spec": None,
                        "explanation": "LLM classified as hybrid search"}

        # BUG-3 fix: LLM 不可用时，基于规则信号判断路由，而非盲目降级
        if self._is_structured(question):
            from src.models.query_dsl import QuerySpec
            spec = QuerySpec.from_json({"filter": {"fulltext": question}})
            return {"mode": "structured", "query_spec": spec,
                    "explanation": "rule-based structured (LLM unavailable)"}

        logging.debug("route_query: no rule/LLM match, fallback to hybrid for query=%r",
                       question[:50])
        return {"mode": "hybrid", "query_spec": None, "explanation": "fallback to hybrid search"}

    def _is_structured(self, question: str) -> bool:
        """LLM 不可用时的兜底：仅匹配强信号词，避免"哪些/状态/包含"等
        日常词把纯语义查询误判为 structured。"""
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

    def _try_llm(self, question: str) -> dict | None:
        llm = self._llm
        if llm is None:
            try:
                from src.core.container import get_active_container
                container = get_active_container()
                if container is not None and container.llm is not None:
                    self._llm = container.llm
                    llm = self._llm
                else:
                    return None
            except Exception as exc:
                logging.debug("route_query: LLM container lookup failed: %s", exc)
                return None
        if llm is None:
            return None
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
                return {"mode": "hybrid"}
            if "query" in parsed:
                spec = QuerySpec.from_json(parsed["query"])
                result = {"mode": parsed.get("mode", "structured"), "query_spec": spec}
                if "traverse" in parsed:
                    result["traverse"] = parsed["traverse"]
                return result
            return None
        except Exception as exc:
            logging.debug("route_query: LLM routing failed (auth/unavailable/parse): %s", exc)
            return None

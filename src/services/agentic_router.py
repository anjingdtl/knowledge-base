import json
import re

from src.models.query_dsl import QuerySpec
from src.services.db import Database

_SYSTEM_PROMPT = """You are a query translator. Convert the user's natural language question into a JSON query DSL.

The DSL supports these filter types:
- {"tag": "tag_name"} — filter by tag
- {"property": {"key": "prop_name", "op": "eq|ne|gt|gte|lt|lte|in|contains|like", "value": ...}} — filter by property
- {"fulltext": "search text"} — full-text search
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

    def route(self, question: str) -> dict:
        if self._is_structured(question):
            dsl = self._try_rule_based(question)
            if dsl is not None:
                return {"mode": "structured", "query_spec": dsl, "explanation": "rule-based routing"}

        if self._is_graph_query(question):
            dsl = self._try_llm(question)
            if dsl is not None:
                return {"mode": "graph", "query_spec": dsl.get("query_spec"),
                        "traverse": dsl.get("traverse", {"max_depth": 2}),
                        "explanation": "LLM graph routing"}

        dsl = self._try_llm(question)
        if dsl is not None and dsl.get("mode") == "structured":
            return {"mode": "structured", "query_spec": dsl["query_spec"],
                    "explanation": "LLM structured routing"}

        return {"mode": "hybrid", "query_spec": None, "explanation": "fallback to hybrid search"}

    def _is_structured(self, question: str) -> bool:
        lower = question.lower()
        return any(signal in lower for signal in _LOGIC_SIGNALS)

    def _is_graph_query(self, question: str) -> bool:
        lower = question.lower()
        return any(signal in lower for signal in _GRAPH_SIGNALS)

    def _try_rule_based(self, question: str) -> "QuerySpec | None":
        conditions = []

        for m in _NL_TAG_RE.finditer(question):
            conditions.append({"tag": m.group(1)})

        for m in _NL_PROP_RE.finditer(question):
            key = m.group(1)
            value = m.group(2)
            if key.lower() in _TAG_KEYWORDS:
                continue
            key = _PROP_NAME_MAP.get(key, key)
            conditions.append({"property": {"key": key, "op": "eq", "value": value}})

        for m in _NL_LINK_RE.finditer(question):
            title = m.group(1).strip()
            conditions.append({"link": f"[[{title}]]"})

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

        if not conditions:
            return None
        if len(conditions) == 1:
            filter_data = conditions[0]
        else:
            filter_data = {"and": conditions}
        return QuerySpec.from_json({"filter": filter_data})

    def _try_llm(self, question: str) -> dict | None:
        llm = self._llm
        if llm is None:
            try:
                from src.core.container import create_container
                container = create_container()
                self._llm = container.llm
                llm = self._llm
            except Exception:
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
        except Exception:
            return None

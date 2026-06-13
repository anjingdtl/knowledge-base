"""Rules-based router for exact logic queries vs fuzzy hybrid retrieval."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from src.services.block_context import BlockContextService
from src.services.db import Database
from src.services.hybrid_search import HybridSearcher


@dataclass
class QueryIntent:
    mode: str
    tags: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    link_titles: list[str] = field(default_factory=list)


class QueryRouter:
    _TAG_RE = re.compile(r"#([\w\u4e00-\u9fff-]+)")
    _PROP_RE = re.compile(r"::([\w\u4e00-\u9fff-]+)\s+([^，,。\s]+)")
    _LINK_RE = re.compile(r"\[\[([^]\n#]+)(?:#[^]\n]+)?\]\]")
    _LOGIC_WORDS = ("所有", "状态", "属于", "筛选", "查找", "找出")

    def __init__(self, db=None, hybrid_searcher=None, context_service=None):
        self._db = db or Database
        self._hybrid = hybrid_searcher or HybridSearcher()
        self._context = context_service or BlockContextService(db=self._db)

    def route(self, question: str) -> QueryIntent:
        tags = self._TAG_RE.findall(question or "")
        properties = {m.group(1): m.group(2) for m in self._PROP_RE.finditer(question or "")}
        link_titles = [m.group(1).strip() for m in self._LINK_RE.finditer(question or "")]
        mode = "logic" if tags or properties or link_titles or any(w in (question or "") for w in self._LOGIC_WORDS) else "hybrid"
        return QueryIntent(mode=mode, tags=tags, properties=properties, link_titles=link_titles)

    def search(self, question: str, top_k: int = 5) -> list[dict]:
        intent = self.route(question)
        if intent.mode != "logic":
            return self._hybrid.search([question], top_k=top_k)
        return self._search_logic(intent, top_k=top_k)

    def _search_logic(self, intent: QueryIntent, top_k: int) -> list[dict]:
        conditions = []
        params: list = []

        for tag in intent.tags:
            from src.services.tag_hierarchy import TagHierarchyService
            expanded = TagHierarchyService(db=self._db).expand(tag)
            placeholders = ",".join("?" for _ in expanded)
            conditions.append(
                f"EXISTS (SELECT 1 FROM json_each(ki.tags) je WHERE je.value IN ({placeholders}))"
            )
            params.extend(expanded)

        for key, value in intent.properties.items():
            conditions.append(
                "EXISTS (SELECT 1 FROM effective_property_index epi "
                "WHERE epi.block_id = b.id AND epi.prop_key = ? AND epi.prop_value = ?)"
            )
            params.extend([key, value])

        target_ids = []
        for title in intent.link_titles:
            target_id = self._knowledge_id_by_title(title)
            if not target_id:
                return []
            target_ids.append(target_id)
        for target_id in target_ids:
            conditions.append(
                "EXISTS (SELECT 1 FROM entity_refs er "
                "WHERE er.target_id = ? AND ("
                "(er.source_type = 'block' AND er.source_id = b.id) OR "
                "(er.source_type = 'knowledge' AND er.source_id = ki.id)))"
            )
            params.append(target_id)

        if not conditions:
            return []

        sql = (
            "SELECT b.id, b.page_id, b.content, b.block_type, b.properties, ki.title "
            "FROM blocks b JOIN knowledge_items ki ON ki.id = b.page_id "
            "WHERE " + " AND ".join(conditions) + " "
            "ORDER BY ki.updated_at DESC, b.order_idx ASC LIMIT ?"
        )
        rows = self._db.get_conn().execute(sql, params + [top_k]).fetchall()
        results = []
        for row in rows:
            try:
                props = json.loads(row["properties"] or "{}")
            except (json.JSONDecodeError, TypeError):
                props = {}
            result = {
                "id": row["id"],
                "text": row["content"],
                "metadata": {
                    "page_id": row["page_id"],
                    "knowledge_id": row["page_id"],
                    "block_id": row["id"],
                    "title": row["title"],
                    "block_type": row["block_type"],
                    "properties": props,
                },
                "score": 1.0,
                "route": "logic",
            }
            self._context.enrich_result(result)
            results.append(result)
        return results

    def _knowledge_id_by_title(self, title: str) -> str | None:
        row = self._db.get_conn().execute(
            "SELECT id FROM knowledge_items WHERE title = ? LIMIT 1",
            (title,),
        ).fetchone()
        return row["id"] if row else None

    def search_dsl(self, dsl_json: dict, top_k: int | None = None) -> list[dict]:
        from src.models.query_dsl import QuerySpec
        from src.services.query_executor import QueryExecutor

        spec = QuerySpec.from_json(dsl_json)
        if top_k is not None:
            spec.limit = top_k
        executor = QueryExecutor(db=self._db)
        return executor.execute(spec)

from typing import Any, cast

from src.models.query_dsl import Condition, QuerySpec
from src.services.db import Database


class QueryExecutor:
    VALID_CONDITION_TYPES = frozenset({
        "and", "or", "not", "tag", "property", "fulltext",
        "title", "link", "file_type", "source_type",
    })

    def __init__(self, db=None):
        self._db = db or Database

    def execute(self, spec: QuerySpec) -> list[dict]:
        where_parts = ["ki.deleted_at IS NULL"]
        params = []
        needs_fts_join = False

        sql, sql_params, fts = self._compile(spec.filter_condition)
        if sql:
            where_parts.append(sql)
            params.extend(sql_params)
        needs_fts_join = fts

        base_query = "SELECT ki.* FROM knowledge_items ki"
        if needs_fts_join:
            base_query += " JOIN knowledge_fts ON knowledge_fts.rowid = ki.rowid"

        where_clause = " AND ".join(where_parts) if where_parts else "1=1"
        order_clause = self._compile_order_clause(spec)

        full_sql = f"{base_query} WHERE {where_clause} {order_clause} LIMIT ? OFFSET ?"
        params.extend([spec.limit, spec.offset])

        conn = self._db.get_conn()
        rows = [dict(r) for r in conn.execute(full_sql, params).fetchall()]

        if spec.include_blocks:
            for row in rows:
                block_rows = conn.execute(
                    "SELECT * FROM blocks WHERE page_id = ? ORDER BY order_idx",
                    (row["id"],),
                ).fetchall()
                blocks_payload = []
                for b in block_rows:
                    bd = dict(b)
                    # 显式暴露 block_id 别名，便于 Agent 消费
                    bd["block_id"] = bd.get("id", "")
                    blocks_payload.append(bd)
                row["blocks"] = blocks_payload

        return rows

    @staticmethod
    def _compile_order_clause(spec: QuerySpec) -> str:
        terms = list(spec.sort_terms or [(spec.sort_by, spec.sort_order)])
        parts = []
        seen_fields: set[str] = set()
        for field, order in terms:
            order_dir = "ASC" if order == "asc" else "DESC"
            parts.append(f"ki.{field} {order_dir}")
            seen_fields.add(str(field))
        # Stable secondary key so equal primary keys do not reshuffle across pages.
        if "id" not in seen_fields:
            parts.append("ki.id ASC")
        return "ORDER BY " + ", ".join(parts)

    def _compile(self, condition: Condition) -> tuple[str, list, bool]:
        if condition.type not in self.VALID_CONDITION_TYPES:
            return "", [], False
        handler = getattr(self, f"_compile_{condition.type}", None)
        if handler is None:
            return "", [], False
        return cast(tuple[str, list[Any], bool], handler(condition))

    def _compile_and(self, condition: Condition) -> tuple[str, list, bool]:
        parts = []
        all_params = []
        any_fts = False
        for child in condition.children:
            sql, params, fts = self._compile(child)
            if sql:
                parts.append(sql)
                all_params.extend(params)
                any_fts = any_fts or fts
        if not parts:
            return "", [], False
        return "(" + " AND ".join(parts) + ")", all_params, any_fts

    def _compile_or(self, condition: Condition) -> tuple[str, list, bool]:
        parts = []
        all_params = []
        any_fts = False
        for child in condition.children:
            sql, params, fts = self._compile(child)
            if sql:
                parts.append(sql)
                all_params.extend(params)
                any_fts = any_fts or fts
        if not parts:
            return "", [], False
        return "(" + " OR ".join(parts) + ")", all_params, any_fts

    def _compile_not(self, condition: Condition) -> tuple[str, list, bool]:
        sql, params, fts = self._compile(condition.child)
        if not sql:
            return "", [], False
        return f"NOT ({sql})", params, fts

    def _compile_tag(self, condition: Condition) -> tuple[str, list, bool]:
        tags = [condition.value]
        if condition.expand_descendants:
            try:
                from src.services.tag_hierarchy import TagHierarchyService
                tags = TagHierarchyService(db=self._db).expand(condition.value)
            except Exception:
                pass
        placeholders = ",".join("?" for _ in tags)
        return (
            f"EXISTS (SELECT 1 FROM json_each(ki.tags) je WHERE je.value IN ({placeholders}))",
            tags,
            False,
        )

    def _compile_property(self, condition: Condition) -> tuple[str, list, bool]:
        key = condition.key
        op = condition.op
        value = condition.value

        if op == "eq":
            return (
                "EXISTS (SELECT 1 FROM effective_property_index epi "
                "WHERE epi.block_id IN (SELECT id FROM blocks WHERE page_id = ki.id) "
                "AND epi.prop_key = ? AND epi.prop_value = ?)",
                [key, str(value)],
                False,
            )
        if op == "ne":
            return (
                "NOT EXISTS (SELECT 1 FROM effective_property_index epi "
                "WHERE epi.block_id IN (SELECT id FROM blocks WHERE page_id = ki.id) "
                "AND epi.prop_key = ? AND epi.prop_value = ?)",
                [key, str(value)],
                False,
            )
        if op in ("gt", "gte", "lt", "lte"):
            op_map = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                return "", [], False
            return (
                f"EXISTS (SELECT 1 FROM effective_property_index epi "
                f"WHERE epi.block_id IN (SELECT id FROM blocks WHERE page_id = ki.id) "
                f"AND epi.prop_key = ? AND CAST(epi.prop_value AS REAL) {op_map[op]} ?)",
                [key, numeric_value],
                False,
            )
        if op == "in":
            values = [str(v) for v in value]
            placeholders = ",".join("?" for _ in values)
            return (
                f"EXISTS (SELECT 1 FROM effective_property_index epi "
                f"WHERE epi.block_id IN (SELECT id FROM blocks WHERE page_id = ki.id) "
                f"AND epi.prop_key = ? AND epi.prop_value IN ({placeholders}))",
                [key] + values,
                False,
            )
        if op == "contains":
            escaped = str(value).replace("%", "\\%").replace("_", "\\_")
            return (
                "EXISTS (SELECT 1 FROM effective_property_index epi "
                "WHERE epi.block_id IN (SELECT id FROM blocks WHERE page_id = ki.id) "
                "AND epi.prop_key = ? AND epi.prop_value LIKE ? ESCAPE '\\')",
                [key, f"%{escaped}%"],
                False,
            )
        if op == "like":
            return (
                "EXISTS (SELECT 1 FROM effective_property_index epi "
                "WHERE epi.block_id IN (SELECT id FROM blocks WHERE page_id = ki.id) "
                "AND epi.prop_key = ? AND epi.prop_value LIKE ?)",
                [key, value],
                False,
            )
        return "", [], False

    def _compile_fulltext(self, condition: Condition) -> tuple[str, list, bool]:
        """编译 fulltext 条件。

        BUG#1 修复：knowledge_fts 用 unicode61 tokenizer，对 CJK 分词有限
        （单字/CJK 词组在 FTS5 MATCH 下命中不稳定，见 db.search_knowledge 的
        多策略召回）。这里对 query 做 jieba 分词后用 LIKE OR 逐词匹配 title/
        content，保证多词 CJK（如 "CDN 教材"）和单词都能命中，无需依赖 FTS JOIN。
        """
        import jieba

        raw = str(condition.value)
        terms = [w.strip() for w in jieba.cut(raw) if w.strip()]
        if not terms:
            terms = [raw]
        # 同时保留原始整串，覆盖 query 恰好是内容子串的情况
        if raw.strip() and raw.strip() not in terms:
            terms.append(raw.strip())

        # 每词 (title LIKE ? OR content LIKE ?)，词间 OR；% _ 转义防通配符注入
        escaped_terms = []
        for t in terms:
            esc = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            escaped_terms.append(esc)
        or_clauses = " OR ".join(
            ["(ki.title LIKE ? ESCAPE '\\' OR ki.content LIKE ? ESCAPE '\\')"] * len(escaped_terms)
        )
        params: list = []
        for esc in escaped_terms:
            params.extend([f"%{esc}%", f"%{esc}%"])
        return f"({or_clauses})", params, False

    def _compile_title(self, condition: Condition) -> tuple[str, list, bool]:
        value = str(condition.value)
        if condition.op == "eq":
            return "ki.title = ?", [value], False
        if condition.op == "contains":
            escaped = value.replace("%", "\\%").replace("_", "\\_")
            return "ki.title LIKE ? ESCAPE '\\'", [f"%{escaped}%"], False
        if condition.op == "like":
            return "ki.title LIKE ?", [value], False
        return "", [], False

    def _compile_link(self, condition: Condition) -> tuple[str, list, bool]:
        title = condition.value
        if title.startswith("[["):
            title = title.strip("[]")
        return (
            "EXISTS (SELECT 1 FROM entity_refs er "
            "JOIN knowledge_items target_ki ON target_ki.id = er.target_id "
            "WHERE er.source_type IN ('block', 'knowledge') "
            "AND (er.source_id = ki.id OR er.source_id IN "
            "(SELECT id FROM blocks WHERE page_id = ki.id)) "
            "AND target_ki.title = ?)",
            [title],
            False,
        )

    def _compile_file_type(self, condition: Condition) -> tuple[str, list, bool]:
        return "ki.file_type = ?", [condition.value], False

    def _compile_source_type(self, condition: Condition) -> tuple[str, list, bool]:
        return "ki.source_type = ?", [condition.value], False

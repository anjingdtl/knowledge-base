from src.models.query_dsl import Condition, QuerySpec
from src.services.db import Database


class QueryExecutor:
    def __init__(self, db=None):
        self._db = db or Database

    def execute(self, spec: QuerySpec) -> list[dict]:
        where_parts = []
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
        order_dir = "ASC" if spec.sort_order == "asc" else "DESC"
        order_clause = f"ORDER BY ki.{spec.sort_by} {order_dir}"

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
                row["blocks"] = [dict(b) for b in block_rows]

        return rows

    def _compile(self, condition: Condition) -> tuple[str, list, bool]:
        handler = getattr(self, f"_compile_{condition.type}", None)
        if handler is None:
            return "", [], False
        return handler(condition)

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
            return (
                f"EXISTS (SELECT 1 FROM effective_property_index epi "
                f"WHERE epi.block_id IN (SELECT id FROM blocks WHERE page_id = ki.id) "
                f"AND epi.prop_key = ? AND CAST(epi.prop_value AS REAL) {op_map[op]} ?)",
                [key, float(value)],
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
            return (
                "EXISTS (SELECT 1 FROM effective_property_index epi "
                "WHERE epi.block_id IN (SELECT id FROM blocks WHERE page_id = ki.id) "
                "AND epi.prop_key = ? AND epi.prop_value LIKE ?)",
                [key, f"%{value}%"],
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
        from src.utils.chinese_tokenizer import sanitize_fts_query
        safe_query = sanitize_fts_query(condition.value)
        return "knowledge_fts MATCH ?", [safe_query], True

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

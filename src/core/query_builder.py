"""查询构建器 — Pythonic 的声明式知识检索 API

用法:
    from src.core.query_builder import query, has_tag, property, fulltext, has_ref_to

    results = query(
        has_tag("Python"),
        property("priority", "high"),
        fulltext("async patterns"),
        limit=20,
    )

编译为 SQL 查询，支持块级数据模型（blocks + properties + FTS + entity_refs）。
"""
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class QueryClause:
    """查询条件基类"""
    def to_sql(self) -> tuple[str, list]:
        """返回 (sql_fragment, params)"""
        raise NotImplementedError


class HasTag(QueryClause):
    def __init__(self, tag: str):
        self.tag = tag

    def to_sql(self):
        # Use json_each for accurate tag matching instead of fragile LIKE on JSON string.
        # Avoids wildcard injection (%/_) and false positive matches (e.g. 'Python' matching '"Pythonic"').
        return (
            "EXISTS (SELECT 1 FROM json_each(ki.tags) je WHERE je.value = ?)",
            [self.tag],
        )


class HasProperty(QueryClause):
    def __init__(self, key: str, value: str):
        self.key = key
        self.value = value

    def to_sql(self):
        return (
            "EXISTS (SELECT 1 FROM effective_property_index epi "
            "JOIN blocks b ON b.id = epi.block_id AND b.page_id = ki.id "
            "WHERE epi.prop_key = ? AND epi.prop_value = ?)",
            [self.key, self.value],
        )


class FullText(QueryClause):
    def __init__(self, query_text: str):
        self.query_text = query_text

    def to_sql(self):
        # FTS requires special handling (MATCH clause on separate virtual table).
        # The query() function checks isinstance(clause, FullText) directly.
        # Return empty tuple to satisfy the base class contract — callers should
        # also check is_fts() rather than relying on to_sql() returning None.
        return ("", [])

    def is_fts(self) -> bool:
        """Explicit marker that this clause requires FTS virtual table join."""
        return True


class HasRefTo(QueryClause):
    def __init__(self, target_id: str):
        self.target_id = target_id

    def to_sql(self):
        return (
            "EXISTS (SELECT 1 FROM entity_refs er "
            "WHERE er.source_type = 'knowledge' AND er.source_id = ki.id "
            "AND er.target_id = ?)",
            [self.target_id],
        )


class FileType(QueryClause):
    def __init__(self, file_type: str):
        self.file_type = file_type

    def to_sql(self):
        return "ki.file_type = ?", [self.file_type]


class SourceType(QueryClause):
    def __init__(self, source_type: str):
        self.source_type = source_type

    def to_sql(self):
        return "ki.source_type = ?", [self.source_type]


class Or(QueryClause):
    def __init__(self, *clauses: QueryClause):
        self.clauses = list(clauses)

    def to_sql(self):
        parts = []
        all_params = []
        for clause in self.clauses:
            if hasattr(clause, 'is_fts') and clause.is_fts():
                continue
            sql, params = clause.to_sql()
            if sql:
                parts.append(sql)
                all_params.extend(params)
        if not parts:
            return "", []
        return "(" + " OR ".join(parts) + ")", all_params


class Not(QueryClause):
    def __init__(self, clause: QueryClause):
        self.clause = clause

    def to_sql(self):
        if hasattr(self.clause, 'is_fts') and self.clause.is_fts():
            return "", []
        sql, params = self.clause.to_sql()
        if not sql:
            return "", []
        return f"NOT ({sql})", params


# ---- 便捷构造函数 ----

def has_tag(tag: str) -> HasTag:
    return HasTag(tag)


def property(key: str, value: str) -> HasProperty:
    return HasProperty(key, value)


def fulltext(query_text: str) -> FullText:
    return FullText(query_text)


def has_ref_to(target_id: str) -> HasRefTo:
    return HasRefTo(target_id)


def file_type(ft: str) -> FileType:
    return FileType(ft)


def source_type(st: str) -> SourceType:
    return SourceType(st)


# ---- 查询执行 ----

def query(*clauses: QueryClause, limit: int = 100, offset: int = 0,
          sort_by: str = "updated_at", sort_order: str = "DESC",
          db=None) -> list[dict]:
    """执行声明式查询，返回匹配的知识条目列表"""
    from src.services.db import Database
    with (db or Database).get_conn() as conn:

        conditions = []
        params = []
        needs_fts = False
        fts_query = ""

        for clause in clauses:
            if hasattr(clause, 'is_fts') and clause.is_fts():
                needs_fts = True
                fts_query = clause.query_text
            else:
                sql, p = clause.to_sql()
                if sql:
                    conditions.append(sql)
                    params.extend(p)

        # 构建 SQL
        if needs_fts:
            try:
                from src.utils.chinese_tokenizer import sanitize_fts_query
            except ImportError:
                # Fallback: basic FTS5 sanitization (remove special operators)
                import re
                def sanitize_fts_query(q: str) -> str:
                    q = q.strip()
                    q = re.sub(r'[^\w\s一-鿿]', ' ', q)
                    return ' '.join(f'"{t}"' for t in q.split() if t) if q.strip() else ''
            safe_q = sanitize_fts_query(fts_query)
            if safe_q:
                where_parts = ["knowledge_fts MATCH ?"] + conditions
                sql = (
                    "SELECT ki.*, rank as fts_rank FROM knowledge_fts kf "
                    "JOIN knowledge_items ki ON ki.rowid = kf.rowid WHERE "
                    + " AND ".join(where_parts)
                )
                params = [safe_q] + params
            else:
                sql = "SELECT ki.* FROM knowledge_items ki"
                if conditions:
                    sql += " WHERE " + " AND ".join(conditions)
        else:
            sql = "SELECT ki.* FROM knowledge_items ki"
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)

        valid_sorts = {"updated_at", "created_at", "title", "version"}
        sort_by = sort_by if sort_by in valid_sorts else "updated_at"
        sort_order = "DESC" if sort_order.upper() == "DESC" else "ASC"
        # NOTE: sort_by and sort_order are validated against strict whitelists above,
        # so the f-string is safe. Do NOT relax validation without updating this pattern.
        sql += f" ORDER BY {sort_by} {sort_order} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("Query failed: %s SQL: %s", e, sql)
            return []


def to_query_spec(*clauses: QueryClause, limit: int = 100, offset: int = 0,
                  sort_by: str = "updated_at", sort_order: str = "desc"):
    from src.models.query_dsl import Condition, QuerySpec

    def clause_to_condition(clause):
        if isinstance(clause, Or):
            return Condition(type="or", children=[clause_to_condition(c) for c in clause.clauses])
        if isinstance(clause, Not):
            return Condition(type="not", child=clause_to_condition(clause.clause))
        if isinstance(clause, HasTag):
            return Condition(type="tag", value=clause.tag, expand_descendants=True)
        if isinstance(clause, HasProperty):
            return Condition(type="property", key=clause.key, op="eq", value=clause.value)
        if isinstance(clause, FullText):
            return Condition(type="fulltext", value=clause.query_text)
        if isinstance(clause, HasRefTo):
            return Condition(type="link", value=clause.target_id)
        if isinstance(clause, FileType):
            return Condition(type="file_type", value=clause.file_type)
        if isinstance(clause, SourceType):
            return Condition(type="source_type", value=clause.source_type)
        return Condition(type="and", children=[])

    conditions = [clause_to_condition(c) for c in clauses]
    if len(conditions) == 1:
        root = conditions[0]
    else:
        root = Condition(type="and", children=conditions)

    return QuerySpec(
        filter_condition=root,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order,
    )

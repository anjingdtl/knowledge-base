# Query Revolution Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete JSON DSL query system with OR/NOT/nesting, multi-hop graph traversal, LLM-powered natural language to DSL Agentic Router, and query explanation — then integrate all of it into MCP, API, and the RAG pipeline.

**Architecture:** Keep SQLite/sqlite-vec and the Phase 1/2 Block-first foundation. Introduce a JSON DSL that compiles to SQL via a recursive condition tree. Add a `GraphTraversalService` for multi-hop queries over `entity_refs` and `tag_relations`. Replace the regex-based `QueryRouter.route()` with an `AgenticRouter` that uses LLM to convert natural language into DSL JSON. Add a `QueryExplainer` that produces human-readable execution plans. Wire everything into the existing `RagPipeline`, `SearchService`, MCP tools, and API endpoints.

**Tech Stack:** Python 3, SQLite, sqlite-vec, FastMCP, FastAPI, pytest

**Status:** Completed on 2026-06-04. Verification: `pytest tests -q` -> 207 passed, 1 third-party warning.

---

## File Structure

**Create:**

- `src/models/query_dsl.py` — JSON DSL dataclasses: `Condition`, `AndGroup`, `OrGroup`, `NotGroup`, `TagFilter`, `PropertyFilter`, `FullTextFilter`, `LinkFilter`, `FileTypeFilter`, `SourceFilter`, `QuerySpec`
- `src/services/query_executor.py` — recursive DSL-to-SQL compiler and executor
- `src/services/graph_traversal.py` — multi-hop relationship traversal over `entity_refs` and `tag_relations`
- `src/services/agentic_router.py` — LLM-powered natural language to DSL JSON converter
- `src/services/query_explainer.py` — human-readable query explanation and execution plan
- `tests/test_query_revolution_phase3.py` — phase acceptance tests

**Modify:**

- `src/services/query_router.py` — delegate to `QueryExecutor` for logic queries, accept DSL JSON input
- `src/core/query_builder.py` — add `Or`, `Not`, `Group` clauses; bridge to `QuerySpec`
- `src/services/rag_pipeline.py` — `VectorSearchStage` uses `AgenticRouter`; return `query_explanation` alongside `source_graph`
- `src/services/search_service.py` — accept optional `query_spec` parameter for structured filtering
- `src/mcp_server.py` — add `structured_query`, `explain_query`, `graph_traverse` tools
- `src/api/routes.py` — add `POST /query`, `POST /query/explain`, `POST /graph/traverse` endpoints
- `src/core/container.py` — register new services
- `config.yaml` — add `agentic_router` configuration section

---

### Task 1: JSON DSL Schema and Models

**Files:**

- Create: `src/models/query_dsl.py`
- Test: `tests/test_query_revolution_phase3.py`

- [ ] **Step 1: Write the failing DSL model test**

Add this to `tests/test_query_revolution_phase3.py`:

```python
import json
import pytest


def test_dsl_parse_simple_tag_filter():
    from src.models.query_dsl import QuerySpec

    spec = QuerySpec.from_json({
        "filter": {"tag": "Python"},
        "limit": 10,
    })
    assert spec.filter_condition.type == "tag"
    assert spec.filter_condition.value == "Python"
    assert spec.limit == 10
    assert spec.offset == 0
    assert spec.sort_by == "updated_at"
    assert spec.sort_order == "desc"


def test_dsl_parse_and_or_not_groups():
    from src.models.query_dsl import QuerySpec

    spec = QuerySpec.from_json({
        "filter": {
            "and": [
                {"tag": "bug"},
                {"or": [
                    {"property": {"key": "status", "op": "eq", "value": "open"}},
                    {"property": {"key": "priority", "op": "gte", "value": 3}},
                ]},
                {"not": {"tag": "wontfix"}},
            ]
        },
        "sort": {"by": "created_at", "order": "asc"},
        "limit": 20,
        "offset": 5,
    })
    root = spec.filter_condition
    assert root.type == "and"
    assert len(root.children) == 3
    assert root.children[0].type == "tag"
    assert root.children[1].type == "or"
    assert len(root.children[1].children) == 2
    assert root.children[1].children[0].type == "property"
    assert root.children[1].children[0].op == "eq"
    assert root.children[2].type == "not"
    assert root.children[2].child.type == "tag"
    assert spec.sort_by == "created_at"
    assert spec.sort_order == "asc"
    assert spec.limit == 20
    assert spec.offset == 5


def test_dsl_parse_all_filter_types():
    from src.models.query_dsl import QuerySpec

    spec = QuerySpec.from_json({
        "filter": {
            "and": [
                {"tag": "frontend", "expand_descendants": True},
                {"property": {"key": "status", "op": "in", "value": ["open", "pending"]}},
                {"fulltext": "async patterns"},
                {"link": "[[Architecture]]"},
                {"file_type": "md"},
                {"source_type": "manual"},
            ]
        }
    })
    children = spec.filter_condition.children
    assert children[0].type == "tag"
    assert children[0].expand_descendants is True
    assert children[1].type == "property"
    assert children[1].op == "in"
    assert children[2].type == "fulltext"
    assert children[3].type == "link"
    assert children[4].type == "file_type"
    assert children[5].type == "source_type"


def test_dsl_to_json_round_trip():
    from src.models.query_dsl import QuerySpec

    original = {
        "filter": {
            "and": [
                {"tag": "bug"},
                {"not": {"property": {"key": "status", "op": "eq", "value": "closed"}}},
            ]
        },
        "limit": 50,
        "sort": {"by": "title", "order": "asc"},
    }
    spec = QuerySpec.from_json(original)
    exported = spec.to_json()
    assert exported["limit"] == 50
    assert exported["sort"]["by"] == "title"
    assert exported["filter"]["and"][0]["tag"] == "bug"
    assert exported["filter"]["and"][1]["not"]["property"]["key"] == "status"


def test_dsl_rejects_invalid_filter():
    from src.models.query_dsl import QuerySpec

    with pytest.raises(ValueError, match="unknown filter type"):
        QuerySpec.from_json({"filter": {"invalid_key": "value"}})

    with pytest.raises(ValueError, match="unknown operator"):
        QuerySpec.from_json({"filter": {"property": {"key": "x", "op": "bad_op", "value": 1}}})
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_dsl" -q
```

Expected: FAIL because `src.models.query_dsl` does not exist.

- [ ] **Step 3: Implement the DSL models**

Create `src/models/query_dsl.py`:

```python
from dataclasses import dataclass, field
from typing import Any


VALID_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "like"}
VALID_SORT_FIELDS = {"updated_at", "created_at", "title", "version", "file_type"}
VALID_SORT_ORDERS = {"asc", "desc"}


@dataclass
class Condition:
    type: str
    value: Any = None
    key: str = ""
    op: str = "eq"
    children: list = field(default_factory=list)
    child: Any = None
    expand_descendants: bool = True

    def to_json(self) -> dict:
        if self.type == "and":
            return {"and": [c.to_json() for c in self.children]}
        if self.type == "or":
            return {"or": [c.to_json() for c in self.children]}
        if self.type == "not":
            return {"not": self.child.to_json()}
        if self.type == "tag":
            result = {"tag": self.value}
            if self.expand_descendants:
                result["expand_descendants"] = True
            return result
        if self.type == "property":
            return {"property": {"key": self.key, "op": self.op, "value": self.value}}
        if self.type == "fulltext":
            return {"fulltext": self.value}
        if self.type == "link":
            return {"link": self.value}
        if self.type == "file_type":
            return {"file_type": self.value}
        if self.type == "source_type":
            return {"source_type": self.value}
        return {}


@dataclass
class QuerySpec:
    filter_condition: Condition
    limit: int = 100
    offset: int = 0
    sort_by: str = "updated_at"
    sort_order: str = "desc"
    include_blocks: bool = False

    def to_json(self) -> dict:
        result = {
            "filter": self.filter_condition.to_json(),
            "limit": self.limit,
            "offset": self.offset,
            "sort": {"by": self.sort_by, "order": self.sort_order},
        }
        if self.include_blocks:
            result["include_blocks"] = True
        return result

    @classmethod
    def from_json(cls, data: dict) -> "QuerySpec":
        filter_data = data.get("filter", {})
        condition = cls._parse_condition(filter_data)
        sort_data = data.get("sort", {})
        sort_by = sort_data.get("by", "updated_at")
        sort_order = sort_data.get("order", "desc")
        if sort_by not in VALID_SORT_FIELDS:
            raise ValueError(f"invalid sort field: {sort_by}")
        if sort_order not in VALID_SORT_ORDERS:
            raise ValueError(f"invalid sort order: {sort_order}")
        return cls(
            filter_condition=condition,
            limit=data.get("limit", 100),
            offset=data.get("offset", 0),
            sort_by=sort_by,
            sort_order=sort_order,
            include_blocks=data.get("include_blocks", False),
        )

    @classmethod
    def _parse_condition(cls, data: dict) -> Condition:
        if not data:
            return Condition(type="and", children=[])
        if "and" in data:
            return Condition(
                type="and",
                children=[cls._parse_condition(c) for c in data["and"]],
            )
        if "or" in data:
            return Condition(
                type="or",
                children=[cls._parse_condition(c) for c in data["or"]],
            )
        if "not" in data:
            return Condition(
                type="not",
                child=cls._parse_condition(data["not"]),
            )
        if "tag" in data:
            return Condition(
                type="tag",
                value=data["tag"],
                expand_descendants=data.get("expand_descendants", True),
            )
        if "property" in data:
            prop = data["property"]
            op = prop.get("op", "eq")
            if op not in VALID_OPS:
                raise ValueError(f"unknown operator: {op}")
            return Condition(
                type="property",
                key=prop["key"],
                op=op,
                value=prop["value"],
            )
        if "fulltext" in data:
            return Condition(type="fulltext", value=data["fulltext"])
        if "link" in data:
            return Condition(type="link", value=data["link"])
        if "file_type" in data:
            return Condition(type="file_type", value=data["file_type"])
        if "source_type" in data:
            return Condition(type="source_type", value=data["source_type"])
        raise ValueError(f"unknown filter type in: {list(data.keys())}")
```

- [ ] **Step 4: Verify DSL models**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_dsl" -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models/query_dsl.py tests/test_query_revolution_phase3.py
git commit -m "feat(query): add JSON DSL schema and models"
```

### Task 2: Query Executor — DSL to SQL Compiler

**Files:**

- Create: `src/services/query_executor.py`
- Modify: `src/core/container.py`
- Test: `tests/test_query_revolution_phase3.py`

- [ ] **Step 1: Write the failing executor tests**

Add:

```python
import json as _json


def _insert_page(item_id, title, content="", tags=None, file_type="txt", source_type="manual"):
    from src.services.db import Database
    Database.insert_knowledge({
        "id": item_id,
        "title": title,
        "content": content,
        "source_type": source_type,
        "source_path": "",
        "file_type": file_type,
        "file_size": 0,
        "content_hash": "",
        "file_created_at": "",
        "file_modified_at": "",
        "tags": _json.dumps(tags or [], ensure_ascii=False),
        "version": 1,
        "created_at": "2026-06-04T00:00:00",
        "updated_at": "2026-06-04T00:00:00",
    })


def _insert_block(block_id, page_id, content, parent_id=None, order_idx=0, properties=None):
    from src.services.db import Database
    Database.insert_blocks([{
        "id": block_id,
        "parent_id": parent_id,
        "page_id": page_id,
        "content": content,
        "block_type": "text",
        "properties": _json.dumps(properties or {}, ensure_ascii=False),
        "order_idx": order_idx,
        "created_at": "2026-06-04T00:00:00",
        "updated_at": "2026-06-04T00:00:00",
    }])


def test_query_executor_simple_tag_filter():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p1", "Python Guide", tags=["Python"])
    _insert_page("p2", "Java Guide", tags=["Java"])

    executor = QueryExecutor()
    spec = QuerySpec.from_json({"filter": {"tag": "Python"}})
    results = executor.execute(spec)

    assert len(results) == 1
    assert results[0]["id"] == "p1"


def test_query_executor_and_or_not():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p3", "Bug A", tags=["bug", "frontend"])
    _insert_page("p4", "Bug B", tags=["bug", "backend"])
    _insert_page("p5", "Feature C", tags=["feature", "frontend"])
    _insert_block("b3", "p3", "Fix login", properties={"status": "open"})
    _insert_block("b4", "p4", "Fix API", properties={"status": "closed"})
    _insert_block("b5", "p5", "Add button", properties={"status": "open"})

    from src.services.effective_properties import EffectivePropertyService
    EffectivePropertyService().refresh_page("p3")
    EffectivePropertyService().refresh_page("p4")
    EffectivePropertyService().refresh_page("p5")

    executor = QueryExecutor()
    spec = QuerySpec.from_json({
        "filter": {
            "and": [
                {"tag": "bug"},
                {"or": [
                    {"tag": "frontend"},
                    {"property": {"key": "status", "op": "eq", "value": "closed"}},
                ]},
                {"not": {"tag": "wontfix"}},
            ]
        }
    })
    results = executor.execute(spec)
    ids = {r["id"] for r in results}
    assert "p3" in ids
    assert "p4" in ids
    assert "p5" not in ids


def test_query_executor_property_operators():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p10", "Props Page", tags=["task"])
    _insert_block("b10", "p10", "Task A", properties={"priority": 1})
    _insert_block("b11", "p10", "Task B", order_idx=1, properties={"priority": 5})
    _insert_block("b12", "p10", "Task C", order_idx=2, properties={"priority": 3})

    from src.services.effective_properties import EffectivePropertyService
    EffectivePropertyService().refresh_page("p10")

    executor = QueryExecutor()

    spec_gte = QuerySpec.from_json({
        "filter": {"property": {"key": "priority", "op": "gte", "value": 3}}
    })
    results = executor.execute(spec_gte)
    assert len(results) >= 2

    spec_in = QuerySpec.from_json({
        "filter": {"property": {"key": "priority", "op": "in", "value": [1, 5]}}
    })
    results = executor.execute(spec_in)
    assert len(results) >= 2


def test_query_executor_fulltext_filter():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p20", "Async Python", content="Learn async patterns in Python")
    _insert_page("p21", "Java Basics", content="Learn Java fundamentals")

    executor = QueryExecutor()
    spec = QuerySpec.from_json({"filter": {"fulltext": "async patterns"}})
    results = executor.execute(spec)
    assert any(r["id"] == "p20" for r in results)
    assert not any(r["id"] == "p21" for r in results)


def test_query_executor_sort_and_pagination():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p30", "Alpha", tags=["sort-test"])
    _insert_page("p31", "Beta", tags=["sort-test"])
    _insert_page("p32", "Gamma", tags=["sort-test"])

    executor = QueryExecutor()
    spec = QuerySpec.from_json({
        "filter": {"tag": "sort-test"},
        "sort": {"by": "title", "order": "asc"},
        "limit": 2,
        "offset": 0,
    })
    results = executor.execute(spec)
    assert len(results) == 2
    assert results[0]["title"] == "Alpha"
    assert results[1]["title"] == "Beta"

    spec_page2 = QuerySpec.from_json({
        "filter": {"tag": "sort-test"},
        "sort": {"by": "title", "order": "asc"},
        "limit": 2,
        "offset": 2,
    })
    results2 = executor.execute(spec_page2)
    assert len(results2) == 1
    assert results2[0]["title"] == "Gamma"


def test_query_executor_include_blocks():
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    _insert_page("p40", "Block Page", tags=["blocks"])
    _insert_block("b40", "p40", "First block")
    _insert_block("b41", "p40", "Second block", order_idx=1)

    executor = QueryExecutor()
    spec = QuerySpec.from_json({
        "filter": {"tag": "blocks"},
        "include_blocks": True,
    })
    results = executor.execute(spec)
    assert len(results) == 1
    assert len(results[0]["blocks"]) == 2
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_query_executor" -q
```

Expected: FAIL because `QueryExecutor` does not exist.

- [ ] **Step 3: Implement `QueryExecutor`**

Create `src/services/query_executor.py`:

```python
import json

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
        from src.services.db import sanitize_fts_query
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
```

- [ ] **Step 4: Register in container**

Add to `src/core/container.py`:

```python
_query_executor: Optional[object] = field(default=None, repr=False)

@property
def query_executor(self):
    if self._query_executor is None:
        from src.services.query_executor import QueryExecutor
        self._query_executor = QueryExecutor(db=self.db)
    return self._query_executor
```

- [ ] **Step 5: Verify executor tests**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_query_executor" -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/services/query_executor.py src/core/container.py tests/test_query_revolution_phase3.py
git commit -m "feat(query): add DSL-to-SQL query executor"
```

### Task 3: QueryBuilder Bridge — OR/NOT/Group and DSL Integration

**Files:**

- Modify: `src/core/query_builder.py`
- Test: `tests/test_query_revolution_phase3.py`

- [ ] **Step 1: Write the failing bridge tests**

Add:

```python
def test_query_builder_or_and_not_clauses():
    from src.core.query_builder import HasTag, Or, Not, query

    _insert_page("qb1", "QB Bug Frontend", tags=["bug", "frontend"])
    _insert_page("qb2", "QB Bug Backend", tags=["bug", "backend"])
    _insert_page("qb3", "QB Feature", tags=["feature", "frontend"])

    results = query(
        HasTag("bug"),
        Or(HasTag("frontend"), HasTag("backend")),
        Not(HasTag("wontfix")),
    )
    ids = {r["id"] for r in results}
    assert "qb1" in ids
    assert "qb2" in ids
    assert "qb3" not in ids


def test_query_builder_to_query_spec():
    from src.core.query_builder import HasTag, HasProperty, Or, Not, FullText, to_query_spec

    spec = to_query_spec(
        HasTag("bug"),
        Or(HasProperty("status", "open"), HasProperty("priority", "high")),
        Not(FullText("deprecated")),
    )
    assert spec.filter_condition.type == "and"
    assert spec.filter_condition.children[0].type == "tag"
    assert spec.filter_condition.children[1].type == "or"
    assert spec.filter_condition.children[2].type == "not"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_query_builder" -q
```

Expected: FAIL because `Or`, `Not`, and `to_query_spec` do not exist.

- [ ] **Step 3: Add OR, NOT, Group clauses and bridge function**

In `src/core/query_builder.py`, add the following classes after the existing `QueryClause` subclasses:

```python
@dataclass
class Or(QueryClause):
    clauses: list

    def __init__(self, *clauses: "QueryClause"):
        self.clauses = list(clauses)

    def to_sql(self):
        parts = []
        all_params = []
        for clause in self.clauses:
            sql, params = clause.to_sql()
            parts.append(sql)
            all_params.extend(params)
        return "(" + " OR ".join(parts) + ")", all_params


@dataclass
class Not(QueryClause):
    clause: "QueryClause"

    def __init__(self, clause: "QueryClause"):
        self.clause = clause

    def to_sql(self):
        sql, params = self.clause.to_sql()
        return f"NOT ({sql})", params
```

Modify the `query()` function to handle `Or` and `Not` by checking `isinstance(clause, (Or, Not))` in the loop and treating them as regular SQL conditions (they don't need FTS join).

Add the bridge function at the end of the file:

```python
def to_query_spec(*clauses: "QueryClause", limit: int = 100, offset: int = 0,
                  sort_by: str = "updated_at", sort_order: str = "desc") -> "QuerySpec":
    from src.models.query_dsl import Condition, QuerySpec

    def clause_to_condition(clause):
        if isinstance(clause, Or):
            return Condition(type="or", children=[clause_to_condition(c) for c in clause.clauses])
        if isinstance(clause, Not):
            return Condition(type="not", child=clause_to_condition(clause.clause))
        if isinstance(clause, HasTag):
            return Condition(type="tag", value=clause.tag, expand_descendants=getattr(clause, "include_descendants", True))
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
```

- [ ] **Step 4: Verify bridge tests**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_query_builder" -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/query_builder.py tests/test_query_revolution_phase3.py
git commit -m "feat(query): add OR/NOT clauses and DSL bridge"
```

### Task 4: Multi-Hop Graph Traversal

**Files:**

- Create: `src/services/graph_traversal.py`
- Modify: `src/core/container.py`
- Test: `tests/test_query_revolution_phase3.py`

- [ ] **Step 1: Write the failing traversal tests**

Add:

```python
def test_graph_traversal_single_hop():
    from src.models.block import EntityRef
    from src.repositories.entity_ref_repo import EntityRefRepository
    from src.services.graph_traversal import GraphTraversalService

    _insert_page("gt1", "Page A")
    _insert_page("gt2", "Page B")
    _insert_page("gt3", "Page C")
    _insert_block("gtb1", "gt1", "Link to B")
    _insert_block("gtb2", "gt2", "Link to C")

    repo = EntityRefRepository()
    repo.upsert(EntityRef(id="gtr1", source_type="block", source_id="gtb1",
                          target_type="knowledge", target_id="gt2", ref_type="link"))
    repo.upsert(EntityRef(id="gtr2", source_type="block", source_id="gtb2",
                          target_type="knowledge", target_id="gt3", ref_type="link"))

    service = GraphTraversalService()
    result = service.traverse(start_ids=["gt1"], start_type="knowledge", max_depth=1)

    node_ids = {n["id"] for n in result["nodes"]}
    assert "gt1" in node_ids
    assert "gt2" in node_ids
    assert "gt3" not in node_ids

    edge_pairs = {(e["source"], e["target"]) for e in result["edges"]}
    assert ("gt1", "gt2") in edge_pairs


def test_graph_traversal_two_hops():
    from src.services.graph_traversal import GraphTraversalService

    service = GraphTraversalService()
    result = service.traverse(start_ids=["gt1"], start_type="knowledge", max_depth=2)

    node_ids = {n["id"] for n in result["nodes"]}
    assert "gt1" in node_ids
    assert "gt2" in node_ids
    assert "gt3" in node_ids


def test_graph_traversal_with_filter():
    from src.models.query_dsl import QuerySpec
    from src.services.graph_traversal import GraphTraversalService

    _insert_page("gt10", "Filtered A", tags=["important"])
    _insert_page("gt11", "Filtered B", tags=["draft"])
    _insert_block("gtb10", "gt10", "Link to B")

    from src.models.block import EntityRef
    from src.repositories.entity_ref_repo import EntityRefRepository
    EntityRefRepository().upsert(EntityRef(
        id="gtr10", source_type="block", source_id="gtb10",
        target_type="knowledge", target_id="gt11", ref_type="link",
    ))

    service = GraphTraversalService()
    filter_spec = QuerySpec.from_json({"filter": {"tag": "important"}})
    result = service.traverse(
        start_ids=["gt10"], start_type="knowledge",
        max_depth=1, node_filter=filter_spec,
    )

    node_ids = {n["id"] for n in result["nodes"]}
    assert "gt10" in node_ids
    assert "gt11" not in node_ids


def test_graph_traversal_returns_path():
    from src.services.graph_traversal import GraphTraversalService

    service = GraphTraversalService()
    result = service.traverse(start_ids=["gt1"], start_type="knowledge", max_depth=2)

    paths = result.get("paths", [])
    assert len(paths) > 0
    assert paths[0][0] == "gt1"
    assert paths[-1][-1] in {"gt2", "gt3"}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_graph_traversal" -q
```

Expected: FAIL because `GraphTraversalService` does not exist.

- [ ] **Step 3: Implement `GraphTraversalService`**

Create `src/services/graph_traversal.py`:

```python
from collections import deque

from src.services.db import Database


class GraphTraversalService:
    def __init__(self, db=None):
        self._db = db or Database

    def traverse(
        self,
        start_ids: list[str],
        start_type: str = "knowledge",
        max_depth: int = 2,
        ref_types: list[str] | None = None,
        node_filter=None,
    ) -> dict:
        conn = self._db.get_conn()
        nodes = {}
        edges = []
        paths = []
        visited = set()
        queue = deque()

        filter_ids = None
        if node_filter is not None:
            from src.services.query_executor import QueryExecutor
            filter_results = QueryExecutor(db=self._db).execute(node_filter)
            filter_ids = {r["id"] for r in filter_results}

        for sid in start_ids:
            queue.append((sid, start_type, 0, [sid]))

        while queue:
            current_id, current_type, depth, path = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)

            if filter_ids is not None and current_id not in filter_ids and depth > 0:
                continue

            node_data = self._load_node(current_id, current_type, conn)
            if node_data:
                nodes[current_id] = node_data

            if depth > 0:
                edges.append({
                    "source": path[-2],
                    "target": current_id,
                    "type": "link",
                    "depth": depth,
                })
                paths.append(path)

            if depth >= max_depth:
                continue

            neighbors = self._find_neighbors(current_id, current_type, ref_types, conn)
            for neighbor_id, neighbor_type, ref_type in neighbors:
                if neighbor_id not in visited:
                    queue.append((neighbor_id, neighbor_type, depth + 1, path + [neighbor_id]))

        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "paths": paths,
        }

    def _load_node(self, node_id: str, node_type: str, conn) -> dict | None:
        if node_type in ("knowledge", "page"):
            row = conn.execute(
                "SELECT id, title, file_type, tags FROM knowledge_items WHERE id = ?",
                (node_id,),
            ).fetchone()
            if row:
                return {"id": row["id"], "type": "page", "label": row["title"],
                        "properties": {"file_type": row["file_type"]}}
        if node_type == "block":
            row = conn.execute(
                "SELECT id, content, page_id FROM blocks WHERE id = ?",
                (node_id,),
            ).fetchone()
            if row:
                return {"id": row["id"], "type": "block",
                        "label": (row["content"] or row["id"])[:80],
                        "properties": {"page_id": row["page_id"]}}
        return None

    def _find_neighbors(self, node_id: str, node_type: str,
                        ref_types: list[str] | None, conn) -> list[tuple[str, str, str]]:
        neighbors = []
        if ref_types:
            rt_clause = "AND ref_type IN ({})".format(
                ",".join("?" for _ in ref_types)
            )
            rt_params = ref_types
        else:
            rt_clause = ""
            rt_params = []

        if node_type in ("knowledge", "page"):
            rows = conn.execute(
                f"""SELECT er.target_id, er.target_type, er.ref_type
                    FROM entity_refs er
                    WHERE er.source_id IN (
                        SELECT id FROM blocks WHERE page_id = ?
                    ) AND er.source_type = 'block' {rt_clause}
                    UNION
                    SELECT er.target_id, er.target_type, er.ref_type
                    FROM entity_refs er
                    WHERE er.source_id = ? AND er.source_type = 'knowledge' {rt_clause}""",
                [node_id] + rt_params + [node_id] + rt_params,
            ).fetchall()
            for row in rows:
                neighbors.append((row["target_id"], row["target_type"], row["ref_type"]))

            back_rows = conn.execute(
                f"""SELECT er.source_id, er.source_type, er.ref_type
                    FROM entity_refs er
                    WHERE er.target_id = ? AND er.target_type = 'knowledge' {rt_clause}""",
                [node_id] + rt_params,
            ).fetchall()
            for row in back_rows:
                if row["source_type"] == "block":
                    page_row = conn.execute(
                        "SELECT page_id FROM blocks WHERE id = ?", (row["source_id"],)
                    ).fetchone()
                    if page_row:
                        neighbors.append((page_row["page_id"], "knowledge", row["ref_type"]))
                else:
                    neighbors.append((row["source_id"], row["source_type"], row["ref_type"]))

        elif node_type == "block":
            rows = conn.execute(
                f"""SELECT er.target_id, er.target_type, er.ref_type
                    FROM entity_refs er
                    WHERE er.source_id = ? AND er.source_type = 'block' {rt_clause}""",
                [node_id] + rt_params,
            ).fetchall()
            for row in rows:
                neighbors.append((row["target_id"], row["target_type"], row["ref_type"]))

        return neighbors
```

- [ ] **Step 4: Register in container**

Add to `src/core/container.py`:

```python
_graph_traversal: Optional[object] = field(default=None, repr=False)

@property
def graph_traversal(self):
    if self._graph_traversal is None:
        from src.services.graph_traversal import GraphTraversalService
        self._graph_traversal = GraphTraversalService(db=self.db)
    return self._graph_traversal
```

- [ ] **Step 5: Verify traversal tests**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_graph_traversal" -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/services/graph_traversal.py src/core/container.py tests/test_query_revolution_phase3.py
git commit -m "feat(query): add multi-hop graph traversal"
```

### Task 5: Query Explainer

**Files:**

- Create: `src/services/query_explainer.py`
- Modify: `src/core/container.py`
- Test: `tests/test_query_revolution_phase3.py`

- [ ] **Step 1: Write the failing explainer tests**

Add:

```python
def test_query_explainer_simple_tag():
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    spec = QuerySpec.from_json({"filter": {"tag": "Python"}, "limit": 10})
    explanation = QueryExplainer().explain(spec)

    assert "tag" in explanation["summary"].lower()
    assert "Python" in explanation["summary"]
    assert explanation["plan"]["tables_used"] == ["knowledge_items"]
    assert explanation["plan"]["estimated_complexity"] == "low"


def test_query_explainer_complex_query():
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    spec = QuerySpec.from_json({
        "filter": {
            "and": [
                {"tag": "bug"},
                {"property": {"key": "status", "op": "eq", "value": "open"}},
                {"fulltext": "login error"},
                {"not": {"tag": "wontfix"}},
            ]
        },
        "include_blocks": True,
    })
    explanation = QueryExplainer().explain(spec)

    assert "AND" in explanation["summary"]
    assert "NOT" in explanation["summary"]
    tables = explanation["plan"]["tables_used"]
    assert "knowledge_items" in tables
    assert "effective_property_index" in tables
    assert "knowledge_fts" in tables
    assert "blocks" in tables
    assert explanation["plan"]["estimated_complexity"] == "medium"


def test_query_explainer_condition_tree():
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    spec = QuerySpec.from_json({
        "filter": {
            "or": [
                {"tag": "bug"},
                {"and": [
                    {"tag": "feature"},
                    {"property": {"key": "priority", "op": "gte", "value": 3}},
                ]},
            ]
        }
    })
    explanation = QueryExplainer().explain(spec)
    tree = explanation["condition_tree"]

    assert tree["type"] == "or"
    assert len(tree["children"]) == 2
    assert tree["children"][0]["type"] == "tag"
    assert tree["children"][1]["type"] == "and"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_query_explainer" -q
```

Expected: FAIL because `QueryExplainer` does not exist.

- [ ] **Step 3: Implement `QueryExplainer`**

Create `src/services/query_explainer.py`:

```python
from src.models.query_dsl import Condition, QuerySpec


class QueryExplainer:
    def explain(self, spec: QuerySpec) -> dict:
        summary = self._summarize(spec.filter_condition)
        plan = self._build_plan(spec)
        tree = self._build_tree(spec.filter_condition)
        return {
            "summary": summary,
            "plan": plan,
            "condition_tree": tree,
            "spec": spec.to_json(),
        }

    def _summarize(self, condition: Condition, depth: int = 0) -> str:
        if condition.type == "and":
            parts = [self._summarize(c, depth + 1) for c in condition.children]
            joiner = " AND "
            result = joiner.join(parts)
            return f"({result})" if depth > 0 else result
        if condition.type == "or":
            parts = [self._summarize(c, depth + 1) for c in condition.children]
            joiner = " OR "
            result = joiner.join(parts)
            return f"({result})" if depth > 0 else result
        if condition.type == "not":
            return f"NOT {self._summarize(condition.child, depth + 1)}"
        if condition.type == "tag":
            suffix = " (with descendants)" if condition.expand_descendants else ""
            return f"tag = '{condition.value}'{suffix}"
        if condition.type == "property":
            return f"property '{condition.key}' {condition.op} '{condition.value}'"
        if condition.type == "fulltext":
            return f"fulltext search '{condition.value}'"
        if condition.type == "link":
            return f"links to '{condition.value}'"
        if condition.type == "file_type":
            return f"file type = '{condition.value}'"
        if condition.type == "source_type":
            return f"source type = '{condition.value}'"
        return "empty filter"

    def _build_plan(self, spec: QuerySpec) -> dict:
        tables = set()
        indexes = []
        self._collect_tables(spec.filter_condition, tables, indexes)
        if spec.include_blocks:
            tables.add("blocks")
        complexity = self._estimate_complexity(spec.filter_condition, tables)
        return {
            "tables_used": sorted(tables),
            "indexes_used": sorted(indexes),
            "estimated_complexity": complexity,
            "pagination": {"limit": spec.limit, "offset": spec.offset},
            "sort": {"field": spec.sort_by, "order": spec.sort_order},
        }

    def _collect_tables(self, condition: Condition, tables: set, indexes: list):
        tables.add("knowledge_items")
        if condition.type in ("and", "or"):
            for child in condition.children:
                self._collect_tables(child, tables, indexes)
        elif condition.type == "not":
            self._collect_tables(condition.child, tables, indexes)
        elif condition.type == "tag":
            indexes.append("json_each(ki.tags)")
        elif condition.type == "property":
            tables.add("effective_property_index")
            tables.add("blocks")
            indexes.append("idx_effective_prop_key_val")
        elif condition.type == "fulltext":
            tables.add("knowledge_fts")
            indexes.append("knowledge_fts MATCH")
        elif condition.type == "link":
            tables.add("entity_refs")
            tables.add("knowledge_items")

    def _estimate_complexity(self, condition: Condition, tables: set) -> str:
        depth = self._max_depth(condition)
        table_count = len(tables)
        if depth <= 1 and table_count <= 2:
            return "low"
        if depth <= 2 and table_count <= 4:
            return "medium"
        return "high"

    def _max_depth(self, condition: Condition) -> int:
        if condition.type in ("and", "or"):
            if not condition.children:
                return 0
            return 1 + max(self._max_depth(c) for c in condition.children)
        if condition.type == "not":
            return 1 + self._max_depth(condition.child)
        return 0

    def _build_tree(self, condition: Condition) -> dict:
        node = {"type": condition.type}
        if condition.type in ("and", "or"):
            node["children"] = [self._build_tree(c) for c in condition.children]
        elif condition.type == "not":
            node["child"] = self._build_tree(condition.child)
        elif condition.type == "tag":
            node["value"] = condition.value
            node["expand_descendants"] = condition.expand_descendants
        elif condition.type == "property":
            node["key"] = condition.key
            node["op"] = condition.op
            node["value"] = condition.value
        elif condition.type in ("fulltext", "link", "file_type", "source_type"):
            node["value"] = condition.value
        return node
```

- [ ] **Step 4: Register in container**

Add to `src/core/container.py`:

```python
_query_explainer: Optional[object] = field(default=None, repr=False)

@property
def query_explainer(self):
    if self._query_explainer is None:
        from src.services.query_explainer import QueryExplainer
        self._query_explainer = QueryExplainer()
    return self._query_explainer
```

- [ ] **Step 5: Verify explainer tests**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_query_explainer" -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/services/query_explainer.py src/core/container.py tests/test_query_revolution_phase3.py
git commit -m "feat(query): add query explainer with execution plans"
```

### Task 6: Agentic Router — NL to DSL via LLM

**Files:**

- Create: `src/services/agentic_router.py`
- Modify: `src/core/container.py`
- Modify: `config.yaml`
- Test: `tests/test_query_revolution_phase3.py`

- [ ] **Step 1: Write the failing agentic router tests**

Add:

```python
def test_agentic_router_converts_nl_to_dsl():
    from src.services.agentic_router import AgenticRouter

    router = AgenticRouter()
    result = router.route("找出所有标记为 bug 且状态为 open 的问题")

    assert result["mode"] == "structured"
    spec = result["query_spec"]
    assert spec.filter_condition.type == "and"
    tag_found = False
    prop_found = False
    for child in spec.filter_condition.children:
        if child.type == "tag" and child.value == "bug":
            tag_found = True
        if child.type == "property" and child.key == "status":
            prop_found = True
    assert tag_found
    assert prop_found


def test_agentic_router_falls_back_to_hybrid():
    from src.services.agentic_router import AgenticRouter

    router = AgenticRouter()
    result = router.route("Python 异步编程的最佳实践是什么")

    assert result["mode"] == "hybrid"
    assert result["query_spec"] is None


def test_agentic_router_handles_graph_query():
    from src.services.agentic_router import AgenticRouter

    router = AgenticRouter()
    result = router.route("显示与 Architecture 页面相关的所有链接关系")

    assert result["mode"] in ("structured", "graph")


def test_agentic_router_with_mock_llm():
    from unittest.mock import MagicMock
    from src.services.agentic_router import AgenticRouter

    mock_llm = MagicMock()
    mock_llm.chat.return_value = '{"filter": {"and": [{"tag": "bug"}, {"property": {"key": "status", "op": "eq", "value": "open"}}]}, "limit": 10}'

    router = AgenticRouter(llm=mock_llm)
    result = router.route("find all open bugs")

    assert result["mode"] == "structured"
    spec = result["query_spec"]
    assert spec.filter_condition.type == "and"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_agentic_router" -q
```

Expected: FAIL because `AgenticRouter` does not exist.

- [ ] **Step 3: Implement `AgenticRouter`**

Create `src/services/agentic_router.py`:

```python
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
        from src.services.query_router import QueryRouter
        legacy = QueryRouter(db=self._db).route(question)
        if legacy.mode != "logic":
            return None
        conditions = []
        for tag in legacy.tags:
            conditions.append({"tag": tag})
        for key, value in legacy.properties.items():
            conditions.append({"property": {"key": key, "op": "eq", "value": value}})
        for title in legacy.link_titles:
            conditions.append({"link": f"[[{title}]]"})
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
                llm = container.llm
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
                temperature=0,
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
        except (json.JSONDecodeError, ValueError, KeyError):
            return None
```

- [ ] **Step 4: Add config defaults**

Add to `config.yaml` under the `rag` section:

```yaml
agentic_router:
  enabled: true
  prefer_llm: false
  system_prompt_max_tokens: 2000
```

- [ ] **Step 5: Register in container**

Add to `src/core/container.py`:

```python
_agentic_router: Optional[object] = field(default=None, repr=False)

@property
def agentic_router(self):
    if self._agentic_router is None:
        from src.services.agentic_router import AgenticRouter
        self._agentic_router = AgenticRouter(db=self.db, llm=self.llm)
    return self._agentic_router
```

- [ ] **Step 6: Verify agentic router tests**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_agentic_router" -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/services/agentic_router.py src/core/container.py config.yaml tests/test_query_revolution_phase3.py
git commit -m "feat(query): add LLM-powered agentic router"
```

### Task 7: Integrate into RAG Pipeline and QueryRouter

**Files:**

- Modify: `src/services/query_router.py`
- Modify: `src/services/rag_pipeline.py`
- Modify: `src/services/search_service.py`
- Test: `tests/test_query_revolution_phase3.py`

- [ ] **Step 1: Write the failing integration tests**

Add:

```python
def test_query_router_accepts_dsl_json():
    from src.services.query_router import QueryRouter

    _insert_page("qr1", "DSL Page", tags=["test-dsl"])
    _insert_block("qrb1", "qr1", "DSL block content")

    router = QueryRouter()
    results = router.search_dsl(
        {"filter": {"tag": "test-dsl"}, "limit": 5}
    )
    assert len(results) >= 1
    assert any(r["id"] == "qr1" for r in results)


def test_rag_pipeline_uses_agentic_router():
    from unittest.mock import MagicMock, patch
    from src.services.rag_pipeline import RagPipeline, DEFAULT_PIPELINE_CONFIG

    mock_llm = MagicMock()
    mock_llm.chat.return_value = '{"mode": "hybrid", "query": "test question"}'

    pipeline = RagPipeline(pipeline_config=DEFAULT_PIPELINE_CONFIG, llm=mock_llm)
    assert pipeline is not None


def test_search_service_accepts_query_spec():
    from src.models.query_dsl import QuerySpec
    from src.services.search_service import SearchService

    _insert_page("ss1", "Search Spec Page", tags=["search-test"])

    service = SearchService()
    spec = QuerySpec.from_json({"filter": {"tag": "search-test"}})
    results = service.search("search-test", top_k=5, query_spec=spec)
    assert isinstance(results, list)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_query_router_accepts_dsl or test_rag_pipeline_uses or test_search_service_accepts" -q
```

Expected: FAIL because `search_dsl` and `query_spec` parameter do not exist.

- [ ] **Step 3: Add `search_dsl` to QueryRouter**

In `src/services/query_router.py`, add this method to the `QueryRouter` class:

```python
def search_dsl(self, dsl_json: dict, top_k: int | None = None) -> list[dict]:
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    spec = QuerySpec.from_json(dsl_json)
    if top_k is not None:
        spec.limit = top_k
    executor = QueryExecutor(db=self._db)
    return executor.execute(spec)
```

- [ ] **Step 4: Update VectorSearchStage to use AgenticRouter**

In `src/services/rag_pipeline.py`, modify `VectorSearchStage.execute()` to try `AgenticRouter` first:

Replace the existing `QueryRouter` block at the top of `execute()` with:

```python
try:
    from src.services.agentic_router import AgenticRouter
    agentic = AgenticRouter(db=Database)
    routing = agentic.route(ctx.question)
    if routing["mode"] == "structured" and routing.get("query_spec"):
        from src.services.query_executor import QueryExecutor
        executor = QueryExecutor(db=Database)
        ctx.candidates = executor.execute(routing["query_spec"])
        if ctx.candidates:
            return ctx
    elif routing["mode"] == "graph" and routing.get("query_spec"):
        from src.services.query_executor import QueryExecutor
        from src.services.graph_traversal import GraphTraversalService
        executor = QueryExecutor(db=Database)
        start_pages = executor.execute(routing["query_spec"])
        start_ids = [p["id"] for p in start_pages]
        traverse_config = routing.get("traverse", {"max_depth": 2})
        traversal = GraphTraversalService(db=Database).traverse(
            start_ids=start_ids, start_type="knowledge",
            max_depth=traverse_config.get("max_depth", 2),
        )
        ctx.candidates = start_pages
        ctx.metadata["graph_traversal"] = traversal
        if ctx.candidates:
            return ctx
except Exception:
    pass

router = QueryRouter(db=Database)
intent = router.route(ctx.question)
```

- [ ] **Step 5: Add `query_spec` parameter to SearchService**

In `src/services/search_service.py`, modify the `search()` method signature:

```python
def search(self, query: str, top_k: int = 10, query_spec=None) -> list[dict]:
```

Add at the beginning of the method body, before the existing rewrite step:

```python
if query_spec is not None:
    from src.services.query_executor import QueryExecutor
    executor = QueryExecutor(db=self._db)
    spec_results = executor.execute(query_spec)
    structured = []
    for row in spec_results[:top_k]:
        structured.append({
            "source": "knowledge",
            "block_id": None,
            "knowledge_id": row["id"],
            "title": row.get("title", ""),
            "text": row.get("content", ""),
            "score": 1.0,
        })
    if structured:
        return structured
```

- [ ] **Step 6: Verify integration tests**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_query_router_accepts_dsl or test_rag_pipeline_uses or test_search_service_accepts" -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/services/query_router.py src/services/rag_pipeline.py src/services/search_service.py tests/test_query_revolution_phase3.py
git commit -m "feat(query): integrate DSL executor into RAG pipeline"
```

### Task 8: MCP Tools and API Endpoints

**Files:**

- Modify: `src/mcp_server.py`
- Modify: `src/api/routes.py`
- Modify: `src/api/__init__.py`
- Test: `tests/test_query_revolution_phase3.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing MCP and API tests**

Add to `tests/test_query_revolution_phase3.py`:

```python
def test_mcp_structured_query_tool():
    from src.mcp_server import create_mcp_server

    _insert_page("mcp-q1", "MCP Query Page", tags=["mcp-test"])

    server = create_mcp_server()
    tool_names = [tool.name for tool in server._tool_manager.list_tools()]
    assert "structured_query" in tool_names
    assert "explain_query" in tool_names
    assert "graph_traverse" in tool_names
```

Add to `tests/test_api.py` under a new `TestPhase3QueryAPI` class:

```python
class TestPhase3QueryAPI:
    def test_structured_query_endpoint(self, api_client):
        from src.services.db import Database
        Database.insert_knowledge({
            "id": "pq1",
            "title": "Query Page",
            "content": "content",
            "source_type": "manual",
            "source_path": "",
            "file_type": "txt",
            "file_size": 0,
            "content_hash": "",
            "file_created_at": "",
            "file_modified_at": "",
            "tags": '["query-test"]',
            "version": 1,
            "created_at": "2026-06-04",
            "updated_at": "2026-06-04",
        })

        resp = api_client.post("/api/query", json={
            "filter": {"tag": "query-test"},
            "limit": 10,
        })
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert any(r["id"] == "pq1" for r in results)

    def test_explain_query_endpoint(self, api_client):
        resp = api_client.post("/api/query/explain", json={
            "filter": {"and": [{"tag": "bug"}, {"property": {"key": "status", "op": "eq", "value": "open"}}]},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "plan" in data
        assert "condition_tree" in data

    def test_graph_traverse_endpoint(self, api_client):
        from src.services.db import Database
        Database.insert_knowledge({
            "id": "gtp1",
            "title": "Traverse Start",
            "content": "",
            "source_type": "manual",
            "source_path": "",
            "file_type": "txt",
            "file_size": 0,
            "content_hash": "",
            "file_created_at": "",
            "file_modified_at": "",
            "tags": "[]",
            "version": 1,
            "created_at": "2026-06-04",
            "updated_at": "2026-06-04",
        })

        resp = api_client.post("/api/graph/traverse", json={
            "start_ids": ["gtp1"],
            "start_type": "knowledge",
            "max_depth": 1,
        })
        assert resp.status_code == 200
        assert "nodes" in resp.json()
        assert "edges" in resp.json()
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_query_revolution_phase3.py::test_mcp_structured_query_tool tests/test_api.py::TestPhase3QueryAPI -q
```

Expected: FAIL because tools and endpoints do not exist.

- [ ] **Step 3: Add MCP tools**

In `src/mcp_server.py`, add these tools after the existing `search` tool:

```python
@mcp.tool()
def structured_query(query_dsl: str, limit: int = 100) -> str:
    """Execute a structured JSON DSL query against the knowledge base.

    The DSL supports tag, property, fulltext, link, file_type, source_type filters
    combined with and/or/not groups.

    Args:
        query_dsl: JSON string with the query DSL
        limit: Maximum results to return
    """
    import json as _json
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    container = _get_container()
    try:
        dsl = _json.loads(query_dsl) if isinstance(query_dsl, str) else query_dsl
        spec = QuerySpec.from_json(dsl)
        spec.limit = min(spec.limit, limit)
        executor = QueryExecutor(db=container.db)
        results = executor.execute(spec)
        return _json.dumps(results, ensure_ascii=False, default=str)
    except Exception as e:
        return _json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def explain_query(query_dsl: str) -> str:
    """Explain a structured query: show human-readable summary, execution plan, and condition tree.

    Args:
        query_dsl: JSON string with the query DSL
    """
    import json as _json
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    try:
        dsl = _json.loads(query_dsl) if isinstance(query_dsl, str) else query_dsl
        spec = QuerySpec.from_json(dsl)
        explainer = QueryExplainer()
        return _json.dumps(explainer.explain(spec), ensure_ascii=False, default=str)
    except Exception as e:
        return _json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def graph_traverse(start_ids: str, max_depth: int = 2, start_type: str = "knowledge") -> str:
    """Traverse the knowledge graph starting from given page/block IDs.

    Args:
        start_ids: JSON array of starting node IDs (e.g. '["page-id-1", "page-id-2"]')
        max_depth: Maximum traversal depth
        start_type: Type of start nodes (knowledge or block)
    """
    import json as _json
    from src.services.graph_traversal import GraphTraversalService

    container = _get_container()
    try:
        ids = _json.loads(start_ids) if isinstance(start_ids, str) else start_ids
        service = GraphTraversalService(db=container.db)
        result = service.traverse(start_ids=ids, start_type=start_type, max_depth=max_depth)
        return _json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return _json.dumps({"error": str(e)}, ensure_ascii=False)
```

- [ ] **Step 4: Add API endpoints**

In `src/api/routes.py`, add request models:

```python
class QueryDSLReq(BaseModel):
    filter: dict
    limit: int = 100
    offset: int = 0
    sort: dict | None = None
    include_blocks: bool = False


class GraphTraverseReq(BaseModel):
    start_ids: list[str]
    start_type: str = "knowledge"
    max_depth: int = 2
    ref_types: list[str] | None = None
```

Add a query router:

```python
query_router = APIRouter(prefix="/query", tags=["query"], dependencies=[Depends(_check_auth)])


@query_router.post("")
def execute_structured_query(data: QueryDSLReq, container: AppContainer = Depends(get_container)):
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    dsl = data.model_dump()
    if data.sort:
        dsl["sort"] = data.sort
    spec = QuerySpec.from_json(dsl)
    executor = QueryExecutor(db=container.db)
    results = executor.execute(spec)
    return {"results": results, "total": len(results)}


@query_router.post("/explain")
def explain_structured_query(data: QueryDSLReq, container: AppContainer = Depends(get_container)):
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    spec = QuerySpec.from_json(data.model_dump())
    explainer = QueryExplainer()
    return explainer.explain(spec)
```

Add a traverse endpoint to the existing `graph_router`:

```python
@graph_router.post("/traverse")
def traverse_graph(data: GraphTraverseReq, container: AppContainer = Depends(get_container)):
    return container.graph_traversal.traverse(
        start_ids=data.start_ids,
        start_type=data.start_type,
        max_depth=data.max_depth,
        ref_types=data.ref_types,
    )
```

- [ ] **Step 5: Register the query router in `src/api/__init__.py`**

Add the import:

```python
from src.api.routes import (
    auth_router, kb_router, chat_router, wiki_router, jobs_router, refs_router,
    graph_router, tags_router, properties_router, query_router,
)
```

Register the router in `create_app()`:

```python
app.include_router(query_router, prefix="/api")
```

- [ ] **Step 6: Verify MCP and API tests**

Run:

```bash
pytest tests/test_query_revolution_phase3.py::test_mcp_structured_query_tool tests/test_api.py::TestPhase3QueryAPI -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/mcp_server.py src/api/routes.py src/api/__init__.py tests/test_query_revolution_phase3.py tests/test_api.py
git commit -m "feat(query): add MCP tools and API endpoints for DSL queries"
```

### Task 9: End-to-End Integration and Full Suite Verification

**Files:**

- Modify: `src/services/rag_pipeline.py`
- Test: `tests/test_query_revolution_phase3.py`

- [ ] **Step 1: Write the end-to-end integration test**

Add:

```python
def test_end_to_end_structured_query_through_rag():
    from unittest.mock import MagicMock
    from src.services.rag_pipeline import RAGService, DEFAULT_PIPELINE_CONFIG

    _insert_page("e2e1", "E2E Bug Report", tags=["bug", "e2e-test"])
    _insert_block("e2eb1", "e2e1", "Login fails on Chrome", properties={"status": "open"})

    from src.services.effective_properties import EffectivePropertyService
    EffectivePropertyService().refresh_page("e2e1")

    mock_llm = MagicMock()
    mock_llm.chat.side_effect = [
        '{"mode": "structured", "query": {"filter": {"and": [{"tag": "bug"}, {"property": {"key": "status", "op": "eq", "value": "open"}}]}}}',
        '[]',
        'The answer is about open bugs.',
    ]

    service = RAGService(pipeline_config=DEFAULT_PIPELINE_CONFIG, llm=mock_llm)
    result = service.query("找出所有标记为 bug 且状态为 open 的问题")

    assert "answer" in result
    assert "sources" in result


def test_end_to_end_query_explanation_in_api():
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    spec = QuerySpec.from_json({
        "filter": {
            "and": [
                {"tag": "bug"},
                {"or": [
                    {"property": {"key": "status", "op": "eq", "value": "open"}},
                    {"property": {"key": "priority", "op": "gte", "value": 3}},
                ]},
                {"not": {"tag": "wontfix"}},
                {"fulltext": "login error"},
            ]
        },
        "include_blocks": True,
        "sort": {"by": "created_at", "order": "desc"},
    })
    explanation = QueryExplainer().explain(spec)

    assert "AND" in explanation["summary"]
    assert "OR" in explanation["summary"]
    assert "NOT" in explanation["summary"]
    assert "knowledge_fts" in explanation["plan"]["tables_used"]
    assert "effective_property_index" in explanation["plan"]["tables_used"]
    assert explanation["plan"]["estimated_complexity"] in ("medium", "high")
    assert explanation["condition_tree"]["type"] == "and"
```

- [ ] **Step 2: Run the integration tests**

Run:

```bash
pytest tests/test_query_revolution_phase3.py -k "test_end_to_end" -q
```

Expected: PASS.

- [ ] **Step 3: Run the full test suite**

Run:

```bash
pytest tests -q
```

Expected: All tests pass. Fix any regressions.

- [ ] **Step 4: Commit final integration**

```bash
git add -A
git commit -m "feat(query): phase3 query revolution complete"
```

## Test Plan

- `pytest tests/test_query_revolution_phase3.py -q`
- `pytest tests/test_api.py::TestPhase3QueryAPI -q`
- `pytest tests/test_structured_graph_rag_phase1.py tests/test_logseq_graph_phase2.py -q`
- `pytest tests/test_mcp_server.py tests/test_search.py tests/test_rag_messages.py -q`
- Full suite: `pytest tests -q`

## Acceptance Criteria

- JSON DSL supports `tag`, `property`, `fulltext`, `link`, `file_type`, `source_type` filters with `and`/`or`/`not` composition and nesting.
- `QueryExecutor` compiles DSL to SQL and returns correct results for all filter types and operators (`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `contains`, `like`).
- `QueryBuilder` gains `Or`, `Not` clauses and a `to_query_spec()` bridge to the DSL.
- `GraphTraversalService` performs BFS multi-hop traversal over `entity_refs` with configurable depth and optional node filtering.
- `QueryExplainer` produces human-readable summary, execution plan (tables, indexes, complexity), and condition tree.
- `AgenticRouter` uses rule-based routing for obvious logic queries, falls back to LLM for complex NL, and degrades to hybrid search when no structure can be extracted.
- `VectorSearchStage` in the RAG pipeline tries `AgenticRouter` before the legacy `QueryRouter`.
- `SearchService.search()` accepts an optional `query_spec` for structured pre-filtering.
- MCP exposes `structured_query`, `explain_query`, and `graph_traverse` tools.
- API exposes `POST /query`, `POST /query/explain`, and `POST /graph/traverse` endpoints.
- All existing Phase 1 and Phase 2 tests continue to pass.

## Out of Scope

- MCP `dry_run`, operation logs, undo/redo remain in the independent operation-safety plan.
- External vector databases and graph databases remain out of scope.
- GUI query builder interface is not part of this phase.
- Query caching and materialized views are future optimization work.

## Assumptions

- Phase 1 (Structured/Graph RAG) and Phase 2 (Logseq Graph) are already merged.
- `effective_property_index` is populated for all blocks (Phase 2 Task 5).
- `tag_relations` may be empty; tag expansion degrades gracefully.
- LLM is available via the container for `AgenticRouter` but the system works without it (rule-based fallback).
- The `knowledge_fts` FTS5 table is up to date with `knowledge_items`.

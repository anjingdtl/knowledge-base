# Logseq Graph Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the second-phase Logseq graph layer: a unified Page/Block/Tag node view, tag DAG inheritance, property type schemas, and effective property propagation.

**Architecture:** Keep the Phase 1 Block-first RAG foundation and SQLite storage. Add focused schema tables for tag relations, property schemas, and effective property indexes, then expose the graph through adapter services instead of replacing `knowledge_items`, `blocks`, `entity_refs`, or existing graph tables. Query and GUI surfaces should consume the new services while preserving old APIs and `sources`/`source_graph` contracts.

**Tech Stack:** Python 3, SQLite, FastAPI, PyQt, pytest

**Status:** Draft only. Do not implement this plan until the user explicitly asks to start Phase 2.

---

## File Structure

**Create:**

- `src/models/unified_node.py` - `UnifiedNode` and `UnifiedEdge` dataclasses for Page/Block/Tag/Property nodes.
- `src/models/tag_relation.py` - `TagRelation` dataclass for parent-child tag edges.
- `src/models/property_schema.py` - `PropertySchema` dataclass and schema serialization helpers.
- `src/repositories/tag_relation_repo.py` - CRUD and cycle-safe storage helpers for tag relations.
- `src/repositories/property_schema_repo.py` - CRUD helpers for global/tag/page/block property schemas.
- `src/services/unified_graph.py` - adapter service that builds a consistent node/edge payload from existing storage.
- `src/services/tag_hierarchy.py` - tag DAG expansion, ancestor lookup, descendant lookup, and cycle detection.
- `src/services/property_schema.py` - property type validation and schema precedence resolution.
- `src/services/effective_properties.py` - property inheritance and `effective_property_index` refresh.
- `tests/test_logseq_graph_phase2.py` - phase acceptance tests covering all Phase 2 capabilities.

**Modify:**

- `src/services/db.py` - add Phase 2 schema tables and `_migrate()` column/table backfills.
- `scripts/migrate_to_block_graph.py` - add Phase 2 tables for offline migrated databases.
- `src/core/container.py` - register repositories and lazy services.
- `src/repositories/block_repo.py` - refresh effective property index after block property replacement.
- `src/services/file_graph.py` - refresh effective properties after page create/update/sync.
- `src/services/query_router.py` - use inherited/effective properties and tag descendants for logic queries.
- `src/core/query_builder.py` - add optional tag descendant expansion and effective property clauses.
- `src/api/routes.py` - expose tag hierarchy, property schema, effective property, and unified graph endpoints.
- `src/gui/graph_view.py` - add a unified graph mode with Page/Block/Tag nodes and effective-property details.

---

### Task 1: Phase 2 Schema and Models

**Files:**

- Create: `src/models/unified_node.py`
- Create: `src/models/tag_relation.py`
- Create: `src/models/property_schema.py`
- Modify: `src/services/db.py`
- Modify: `scripts/migrate_to_block_graph.py`
- Test: `tests/test_logseq_graph_phase2.py`

- [ ] **Step 1: Write the failing schema test**

Add this to `tests/test_logseq_graph_phase2.py`:

```python
import json

from src.services.db import Database


def test_phase2_schema_tables_exist():
    conn = Database.get_conn()
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"tag_relations", "property_schemas", "effective_property_index"}.issubset(tables)

    tag_cols = {row["name"] for row in conn.execute("PRAGMA table_info(tag_relations)").fetchall()}
    assert {"parent_tag", "child_tag", "created_at"}.issubset(tag_cols)

    schema_cols = {row["name"] for row in conn.execute("PRAGMA table_info(property_schemas)").fetchall()}
    assert {
        "id", "scope_type", "scope_id", "property_name", "property_type",
        "required", "default_value", "choices", "constraints", "created_at",
    }.issubset(schema_cols)

    effective_cols = {row["name"] for row in conn.execute("PRAGMA table_info(effective_property_index)").fetchall()}
    assert {
        "block_id", "prop_key", "prop_value", "value_type",
        "source_type", "source_id", "inherited", "updated_at",
    }.issubset(effective_cols)


def test_phase2_models_round_trip():
    from src.models.property_schema import PropertySchema
    from src.models.tag_relation import TagRelation
    from src.models.unified_node import UnifiedEdge, UnifiedNode

    schema = PropertySchema(
        id="schema-1",
        scope_type="tag",
        scope_id="bug",
        property_name="status",
        property_type="text",
        required=1,
        choices=["open", "closed"],
        constraints={"max_length": 20},
    )
    row = schema.to_row()
    assert json.loads(row["choices"]) == ["open", "closed"]
    assert PropertySchema.from_row(row).choices == ["open", "closed"]

    relation = TagRelation(parent_tag="project", child_tag="bug")
    assert relation.to_row()["parent_tag"] == "project"

    node = UnifiedNode(id="block-1", node_type="block", label="Fix login")
    edge = UnifiedEdge(source="page-1", target="block-1", edge_type="contains")
    assert node.to_dict()["type"] == "block"
    assert edge.to_dict()["type"] == "contains"
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_phase2_schema_tables_exist tests/test_logseq_graph_phase2.py::test_phase2_models_round_trip -q
```

Expected: FAIL because the tables and models do not exist.

- [ ] **Step 3: Add the SQLite schema**

Add these tables to `_SCHEMA` in `src/services/db.py` after `block_property_index`:

```sql
CREATE TABLE IF NOT EXISTS tag_relations (
    parent_tag TEXT NOT NULL,
    child_tag TEXT NOT NULL,
    created_at TEXT,
    PRIMARY KEY (parent_tag, child_tag),
    CHECK(parent_tag <> child_tag)
);

CREATE INDEX IF NOT EXISTS idx_tag_relations_parent ON tag_relations(parent_tag);
CREATE INDEX IF NOT EXISTS idx_tag_relations_child ON tag_relations(child_tag);

CREATE TABLE IF NOT EXISTS property_schemas (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT DEFAULT '',
    property_name TEXT NOT NULL,
    property_type TEXT NOT NULL,
    required INTEGER DEFAULT 0,
    default_value TEXT,
    choices TEXT,
    constraints TEXT,
    created_at TEXT,
    UNIQUE(scope_type, scope_id, property_name)
);

CREATE INDEX IF NOT EXISTS idx_property_schemas_scope ON property_schemas(scope_type, scope_id);

CREATE TABLE IF NOT EXISTS effective_property_index (
    block_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    prop_key TEXT NOT NULL,
    prop_value TEXT,
    value_type TEXT DEFAULT 'string',
    source_type TEXT NOT NULL,
    source_id TEXT DEFAULT '',
    inherited INTEGER DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (block_id, prop_key)
);

CREATE INDEX IF NOT EXISTS idx_effective_prop_key_val ON effective_property_index(prop_key, prop_value);
```

Add `_migrate()` guards that create the tables when connecting to an existing database:

```python
cls._conn.execute("""CREATE TABLE IF NOT EXISTS tag_relations (
    parent_tag TEXT NOT NULL,
    child_tag TEXT NOT NULL,
    created_at TEXT,
    PRIMARY KEY (parent_tag, child_tag),
    CHECK(parent_tag <> child_tag)
)""")
cls._conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_relations_parent ON tag_relations(parent_tag)")
cls._conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_relations_child ON tag_relations(child_tag)")
cls._conn.execute("""CREATE TABLE IF NOT EXISTS property_schemas (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT DEFAULT '',
    property_name TEXT NOT NULL,
    property_type TEXT NOT NULL,
    required INTEGER DEFAULT 0,
    default_value TEXT,
    choices TEXT,
    constraints TEXT,
    created_at TEXT,
    UNIQUE(scope_type, scope_id, property_name)
)""")
cls._conn.execute("CREATE INDEX IF NOT EXISTS idx_property_schemas_scope ON property_schemas(scope_type, scope_id)")
cls._conn.execute("""CREATE TABLE IF NOT EXISTS effective_property_index (
    block_id TEXT REFERENCES blocks(id) ON DELETE CASCADE,
    prop_key TEXT NOT NULL,
    prop_value TEXT,
    value_type TEXT DEFAULT 'string',
    source_type TEXT NOT NULL,
    source_id TEXT DEFAULT '',
    inherited INTEGER DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (block_id, prop_key)
)""")
cls._conn.execute("CREATE INDEX IF NOT EXISTS idx_effective_prop_key_val ON effective_property_index(prop_key, prop_value)")
```

Mirror the same schema in `scripts/migrate_to_block_graph.py` inside `BLOCK_GRAPH_SCHEMA` and `_ensure_block_graph_columns()`.

- [ ] **Step 4: Add model dataclasses**

Create `src/models/unified_node.py`:

```python
from dataclasses import dataclass, field


@dataclass
class UnifiedNode:
    id: str
    node_type: str
    label: str
    source_id: str = ""
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.node_type,
            "label": self.label,
            "source_id": self.source_id,
            "properties": self.properties,
        }


@dataclass
class UnifiedEdge:
    source: str
    target: str
    edge_type: str
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.edge_type,
            "properties": self.properties,
        }
```

Create `src/models/tag_relation.py`:

```python
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TagRelation:
    parent_tag: str
    child_tag: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_row(self) -> dict:
        return {
            "parent_tag": self.parent_tag,
            "child_tag": self.child_tag,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "TagRelation":
        return cls(
            parent_tag=row["parent_tag"],
            child_tag=row["child_tag"],
            created_at=row.get("created_at", ""),
        )
```

Create `src/models/property_schema.py`:

```python
from dataclasses import dataclass, field
from datetime import datetime
import json
import uuid


@dataclass
class PropertySchema:
    scope_type: str
    property_name: str
    property_type: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    scope_id: str = ""
    required: int = 0
    default_value: object = None
    choices: list | None = None
    constraints: dict | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id or "",
            "property_name": self.property_name,
            "property_type": self.property_type,
            "required": int(self.required),
            "default_value": json.dumps(self.default_value, ensure_ascii=False) if self.default_value is not None else None,
            "choices": json.dumps(self.choices, ensure_ascii=False) if self.choices is not None else None,
            "constraints": json.dumps(self.constraints, ensure_ascii=False) if self.constraints is not None else None,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "PropertySchema":
        def load(value):
            if value in (None, ""):
                return None
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value

        return cls(
            id=row["id"],
            scope_type=row["scope_type"],
            scope_id=row.get("scope_id") or "",
            property_name=row["property_name"],
            property_type=row["property_type"],
            required=int(row.get("required") or 0),
            default_value=load(row.get("default_value")),
            choices=load(row.get("choices")),
            constraints=load(row.get("constraints")),
            created_at=row.get("created_at", ""),
        )
```

- [ ] **Step 5: Verify schema and models**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_phase2_schema_tables_exist tests/test_logseq_graph_phase2.py::test_phase2_models_round_trip -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/services/db.py scripts/migrate_to_block_graph.py src/models/unified_node.py src/models/tag_relation.py src/models/property_schema.py tests/test_logseq_graph_phase2.py
git commit -m "feat(graph): add phase2 graph schema and models"
```

### Task 2: Unified Page/Block/Tag Node View

**Files:**

- Create: `src/services/unified_graph.py`
- Modify: `src/core/container.py`
- Test: `tests/test_logseq_graph_phase2.py`

- [ ] **Step 1: Write the failing unified graph test**

Add:

```python
def _insert_knowledge(item_id: str, title: str, content: str, tags=None):
    Database.insert_knowledge({
        "id": item_id,
        "title": title,
        "content": content,
        "source_type": "manual",
        "source_path": "",
        "file_type": "txt",
        "file_size": 0,
        "content_hash": "",
        "file_created_at": "",
        "file_modified_at": "",
        "tags": json.dumps(tags or [], ensure_ascii=False),
        "version": 1,
        "created_at": "2026-06-04T00:00:00",
        "updated_at": "2026-06-04T00:00:00",
    })


def _insert_block(block_id: str, page_id: str, content: str, parent_id=None, order_idx=0, properties=None):
    Database.insert_blocks([{
        "id": block_id,
        "parent_id": parent_id,
        "page_id": page_id,
        "content": content,
        "block_type": "text",
        "properties": json.dumps(properties or {}, ensure_ascii=False),
        "order_idx": order_idx,
        "created_at": "2026-06-04T00:00:00",
        "updated_at": "2026-06-04T00:00:00",
    }])


def test_unified_graph_builds_page_block_tag_and_link_edges():
    from src.models.block import EntityRef
    from src.repositories.entity_ref_repo import EntityRefRepository
    from src.services.unified_graph import UnifiedGraphService

    _insert_knowledge("page-1", "Frontend Plan", "content", tags=["bug"])
    _insert_knowledge("page-2", "Project Alpha", "project")
    _insert_block("parent", "page-1", "Tasks")
    _insert_block("child", "page-1", "Fix login", parent_id="parent", order_idx=1)
    EntityRefRepository().upsert(EntityRef(
        id="ref-1",
        source_type="block",
        source_id="child",
        target_type="knowledge",
        target_id="page-2",
        ref_type="link",
    ))

    graph = UnifiedGraphService(db=Database).build(include_blocks=True, include_tags=True)

    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = {(edge["source"], edge["target"], edge["type"]) for edge in graph["edges"]}
    assert nodes["page:page-1"]["type"] == "page"
    assert nodes["block:child"]["type"] == "block"
    assert nodes["tag:bug"]["type"] == "tag"
    assert ("page:page-1", "block:child", "contains") in edges
    assert ("block:parent", "block:child", "parent") in edges
    assert ("page:page-1", "tag:bug", "tagged_with") in edges
    assert ("block:child", "page:page-2", "link") in edges
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_unified_graph_builds_page_block_tag_and_link_edges -q
```

Expected: FAIL because `UnifiedGraphService` does not exist.

- [ ] **Step 3: Implement `UnifiedGraphService`**

Create `src/services/unified_graph.py`:

```python
import json

from src.models.unified_node import UnifiedEdge, UnifiedNode
from src.services.db import Database


class UnifiedGraphService:
    def __init__(self, db=None):
        self._db = db or Database

    def build(self, include_blocks: bool = True, include_tags: bool = True, page_limit: int = 500) -> dict:
        nodes: dict[str, UnifiedNode] = {}
        edges: dict[tuple[str, str, str], UnifiedEdge] = {}

        def add_node(node: UnifiedNode):
            nodes.setdefault(node.id, node)

        def add_edge(edge: UnifiedEdge):
            edges.setdefault((edge.source, edge.target, edge.edge_type), edge)

        pages = self._db.list_knowledge(limit=page_limit)
        for page in pages:
            page_id = f"page:{page['id']}"
            tags = self._load_json_list(page.get("tags"))
            add_node(UnifiedNode(
                id=page_id,
                node_type="page",
                label=page.get("title") or page["id"],
                source_id=page["id"],
                properties={"file_type": page.get("file_type", ""), "tags": tags},
            ))
            if include_tags:
                for tag in tags:
                    tag_id = f"tag:{tag}"
                    add_node(UnifiedNode(id=tag_id, node_type="tag", label=tag, source_id=tag))
                    add_edge(UnifiedEdge(source=page_id, target=tag_id, edge_type="tagged_with"))

        if include_blocks:
            rows = self._db.get_conn().execute(
                "SELECT id, parent_id, page_id, content, block_type, properties FROM blocks ORDER BY page_id, order_idx"
            ).fetchall()
            for row in rows:
                block_id = f"block:{row['id']}"
                add_node(UnifiedNode(
                    id=block_id,
                    node_type="block",
                    label=(row["content"] or row["id"]).replace("\n", " ")[:80],
                    source_id=row["id"],
                    properties={"page_id": row["page_id"], "block_type": row["block_type"]},
                ))
                if row["page_id"]:
                    add_edge(UnifiedEdge(source=f"page:{row['page_id']}", target=block_id, edge_type="contains"))
                if row["parent_id"]:
                    add_edge(UnifiedEdge(source=f"block:{row['parent_id']}", target=block_id, edge_type="parent"))

        ref_rows = self._db.get_conn().execute(
            "SELECT source_type, source_id, target_type, target_id, ref_type FROM entity_refs"
        ).fetchall()
        for ref in ref_rows:
            source = self._node_id(ref["source_type"], ref["source_id"])
            target = self._node_id(ref["target_type"], ref["target_id"])
            if source and target:
                add_edge(UnifiedEdge(source=source, target=target, edge_type=ref["ref_type"] or "link"))

        if include_tags:
            rels = self._db.get_conn().execute("SELECT parent_tag, child_tag FROM tag_relations").fetchall()
            for rel in rels:
                parent = f"tag:{rel['parent_tag']}"
                child = f"tag:{rel['child_tag']}"
                add_node(UnifiedNode(id=parent, node_type="tag", label=rel["parent_tag"], source_id=rel["parent_tag"]))
                add_node(UnifiedNode(id=child, node_type="tag", label=rel["child_tag"], source_id=rel["child_tag"]))
                add_edge(UnifiedEdge(source=parent, target=child, edge_type="tag_parent"))

        return {
            "nodes": [node.to_dict() for node in nodes.values()],
            "edges": [edge.to_dict() for edge in edges.values()],
        }

    def _node_id(self, source_type: str, source_id: str) -> str:
        if source_type in {"knowledge", "page"}:
            return f"page:{source_id}"
        if source_type == "block":
            return f"block:{source_id}"
        if source_type == "tag":
            return f"tag:{source_id}"
        return ""

    def _load_json_list(self, value) -> list[str]:
        if isinstance(value, list):
            return value
        if not value:
            return []
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
```

- [ ] **Step 4: Register the service in the container**

Add an `_unified_graph` field and a lazy property in `src/core/container.py`:

```python
_unified_graph: Optional[object] = field(default=None, repr=False)

@property
def unified_graph(self):
    if self._unified_graph is None:
        from src.services.unified_graph import UnifiedGraphService
        self._unified_graph = UnifiedGraphService(db=self.db)
    return self._unified_graph
```

- [ ] **Step 5: Verify unified graph service**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_unified_graph_builds_page_block_tag_and_link_edges -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/services/unified_graph.py src/core/container.py tests/test_logseq_graph_phase2.py
git commit -m "feat(graph): add unified page block tag graph view"
```

### Task 3: Tag DAG and Tag Inheritance

**Files:**

- Create: `src/repositories/tag_relation_repo.py`
- Create: `src/services/tag_hierarchy.py`
- Modify: `src/core/container.py`
- Modify: `src/core/query_builder.py`
- Modify: `src/services/query_router.py`
- Test: `tests/test_logseq_graph_phase2.py`

- [ ] **Step 1: Write failing tag hierarchy tests**

Add:

```python
def test_tag_hierarchy_expands_descendants_and_rejects_cycles():
    from src.services.tag_hierarchy import TagHierarchyService

    service = TagHierarchyService(db=Database)
    service.add_relation("project", "frontend")
    service.add_relation("frontend", "bug")

    assert service.descendants("project") == ["frontend", "bug"]
    assert service.ancestors("bug") == ["frontend", "project"]

    try:
        service.add_relation("bug", "project")
    except ValueError as exc:
        assert "cycle" in str(exc).lower()
    else:
        raise AssertionError("cycle should be rejected")


def test_tag_inheritance_expands_logic_queries():
    from src.services.query_router import QueryRouter

    _insert_knowledge("bug-page", "Bug Page", "content", tags=["bug"])
    _insert_block("bug-block", "bug-page", "Fix login", properties={"status": "open"})

    from src.services.tag_hierarchy import TagHierarchyService
    TagHierarchyService(db=Database).add_relation("frontend", "bug")

    results = QueryRouter(db=Database).search("#frontend ::status open", top_k=5)

    assert [result["id"] for result in results] == ["bug-block"]
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_tag_hierarchy_expands_descendants_and_rejects_cycles tests/test_logseq_graph_phase2.py::test_tag_inheritance_expands_logic_queries -q
```

Expected: FAIL because `TagHierarchyService` does not exist and queries do not expand descendant tags.

- [ ] **Step 3: Implement the tag repository**

Create `src/repositories/tag_relation_repo.py`:

```python
from src.models.tag_relation import TagRelation
from src.services.db import Database


class TagRelationRepository:
    def __init__(self, db=None):
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    def upsert(self, relation: TagRelation) -> None:
        self._conn().execute(
            """INSERT OR REPLACE INTO tag_relations (parent_tag, child_tag, created_at)
               VALUES (:parent_tag, :child_tag, :created_at)""",
            relation.to_row(),
        )
        self._conn().commit()

    def delete(self, parent_tag: str, child_tag: str) -> int:
        cursor = self._conn().execute(
            "DELETE FROM tag_relations WHERE parent_tag = ? AND child_tag = ?",
            (parent_tag, child_tag),
        )
        self._conn().commit()
        return cursor.rowcount

    def list_all(self) -> list[TagRelation]:
        rows = self._conn().execute(
            "SELECT parent_tag, child_tag, created_at FROM tag_relations ORDER BY parent_tag, child_tag"
        ).fetchall()
        return [TagRelation.from_row(dict(row)) for row in rows]
```

- [ ] **Step 4: Implement `TagHierarchyService`**

Create `src/services/tag_hierarchy.py`:

```python
from collections import defaultdict, deque

from src.models.tag_relation import TagRelation
from src.repositories.tag_relation_repo import TagRelationRepository
from src.services.db import Database


class TagHierarchyService:
    def __init__(self, db=None, repo: TagRelationRepository | None = None):
        self._db = db or Database
        self._repo = repo or TagRelationRepository(db=self._db)

    def add_relation(self, parent_tag: str, child_tag: str) -> None:
        parent_tag = parent_tag.strip()
        child_tag = child_tag.strip()
        if not parent_tag or not child_tag:
            raise ValueError("parent_tag and child_tag are required")
        if parent_tag == child_tag:
            raise ValueError("tag relation would create a cycle")
        if parent_tag in self.descendants(child_tag):
            raise ValueError("tag relation would create a cycle")
        self._repo.upsert(TagRelation(parent_tag=parent_tag, child_tag=child_tag))

    def descendants(self, tag: str) -> list[str]:
        graph = self._children_map()
        return self._walk(graph, tag)

    def ancestors(self, tag: str) -> list[str]:
        graph = self._parents_map()
        return self._walk(graph, tag)

    def expand(self, tag: str, include_self: bool = True) -> list[str]:
        tags = [tag] if include_self else []
        tags.extend(self.descendants(tag))
        return tags

    def _walk(self, graph: dict[str, list[str]], root: str) -> list[str]:
        seen = set()
        ordered = []
        queue = deque(graph.get(root, []))
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            ordered.append(current)
            queue.extend(graph.get(current, []))
        return ordered

    def _children_map(self) -> dict[str, list[str]]:
        graph = defaultdict(list)
        for relation in self._repo.list_all():
            graph[relation.parent_tag].append(relation.child_tag)
        return dict(graph)

    def _parents_map(self) -> dict[str, list[str]]:
        graph = defaultdict(list)
        for relation in self._repo.list_all():
            graph[relation.child_tag].append(relation.parent_tag)
        return dict(graph)
```

- [ ] **Step 5: Register repository and service in the container**

Add fields to `AppContainer`:

```python
tag_relation_repo: "TagRelationRepository" = field(default=None, repr=False)
_tag_hierarchy: Optional[object] = field(default=None, repr=False)
```

Initialize the repository in `create_container()`:

```python
from src.repositories.tag_relation_repo import TagRelationRepository
container.tag_relation_repo = TagRelationRepository(db=Database)
```

Add the service property:

```python
@property
def tag_hierarchy(self):
    if self._tag_hierarchy is None:
        from src.services.tag_hierarchy import TagHierarchyService
        self._tag_hierarchy = TagHierarchyService(db=self.db, repo=self.tag_relation_repo)
    return self._tag_hierarchy
```

- [ ] **Step 6: Expand tags in structured queries**

Modify `src/services/query_router.py` so each requested tag expands through descendants:

```python
from src.services.tag_hierarchy import TagHierarchyService

tag_service = TagHierarchyService(db=self._db)
for tag in intent.tags:
    expanded = tag_service.expand(tag)
    placeholders = ",".join("?" for _ in expanded)
    conditions.append(
        f"EXISTS (SELECT 1 FROM json_each(ki.tags) je WHERE je.value IN ({placeholders}))"
    )
    params.extend(expanded)
```

Modify `src/core/query_builder.py` by changing `HasTag` to accept descendant expansion:

```python
class HasTag(QueryClause):
    def __init__(self, tag: str, include_descendants: bool = True):
        self.tag = tag
        self.include_descendants = include_descendants

    def to_sql(self):
        tags = [self.tag]
        if self.include_descendants:
            from src.services.tag_hierarchy import TagHierarchyService
            tags = TagHierarchyService().expand(self.tag)
        placeholders = ",".join("?" for _ in tags)
        return (
            f"EXISTS (SELECT 1 FROM json_each(ki.tags) je WHERE je.value IN ({placeholders}))",
            tags,
        )
```

- [ ] **Step 7: Verify tag inheritance**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_tag_hierarchy_expands_descendants_and_rejects_cycles tests/test_logseq_graph_phase2.py::test_tag_inheritance_expands_logic_queries -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/repositories/tag_relation_repo.py src/services/tag_hierarchy.py src/core/container.py src/core/query_builder.py src/services/query_router.py tests/test_logseq_graph_phase2.py
git commit -m "feat(graph): add tag hierarchy inheritance"
```

### Task 4: Property Schemas and Validation

**Files:**

- Create: `src/repositories/property_schema_repo.py`
- Create: `src/services/property_schema.py`
- Modify: `src/core/container.py`
- Test: `tests/test_logseq_graph_phase2.py`

- [ ] **Step 1: Write failing property schema tests**

Add:

```python
def test_property_schema_validates_supported_types_and_choices():
    from src.models.property_schema import PropertySchema
    from src.services.property_schema import PropertySchemaService

    service = PropertySchemaService(db=Database)
    service.upsert(PropertySchema(
        scope_type="global",
        property_name="priority",
        property_type="number",
        choices=[1, 2, 3],
    ))
    service.upsert(PropertySchema(
        scope_type="tag",
        scope_id="bug",
        property_name="status",
        property_type="text",
        choices=["open", "closed"],
    ))

    assert service.validate_value("priority", 2, scope_type="global", scope_id="").valid is True
    assert service.validate_value("priority", "high", scope_type="global", scope_id="").valid is False
    assert service.validate_value("status", "open", scope_type="tag", scope_id="bug").valid is True
    assert service.validate_value("status", "pending", scope_type="tag", scope_id="bug").valid is False


def test_property_schema_precedence_global_tag_page_block():
    from src.models.property_schema import PropertySchema
    from src.services.property_schema import PropertySchemaService

    service = PropertySchemaService(db=Database)
    service.upsert(PropertySchema(scope_type="global", property_name="owner", property_type="text", default_value="ops"))
    service.upsert(PropertySchema(scope_type="tag", scope_id="bug", property_name="owner", property_type="text", default_value="frontend"))
    service.upsert(PropertySchema(scope_type="page", scope_id="page-1", property_name="owner", property_type="text", default_value="page-owner"))

    resolved = service.resolve_schema(property_name="owner", page_id="page-1", tags=["bug"], block_id="block-1")

    assert resolved.scope_type == "page"
    assert resolved.default_value == "page-owner"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_property_schema_validates_supported_types_and_choices tests/test_logseq_graph_phase2.py::test_property_schema_precedence_global_tag_page_block -q
```

Expected: FAIL because property schema repo/service do not exist.

- [ ] **Step 3: Implement `PropertySchemaRepository`**

Create `src/repositories/property_schema_repo.py`:

```python
from src.models.property_schema import PropertySchema
from src.services.db import Database


class PropertySchemaRepository:
    def __init__(self, db=None):
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    def upsert(self, schema: PropertySchema) -> PropertySchema:
        row = schema.to_row()
        self._conn().execute(
            """INSERT OR REPLACE INTO property_schemas
               (id, scope_type, scope_id, property_name, property_type, required,
                default_value, choices, constraints, created_at)
               VALUES (:id, :scope_type, :scope_id, :property_name, :property_type,
                       :required, :default_value, :choices, :constraints, :created_at)""",
            row,
        )
        self._conn().commit()
        return schema

    def list_for_scope(self, scope_type: str, scope_id: str = "") -> list[PropertySchema]:
        rows = self._conn().execute(
            """SELECT * FROM property_schemas
               WHERE scope_type = ? AND scope_id = ?
               ORDER BY property_name ASC""",
            (scope_type, scope_id or ""),
        ).fetchall()
        return [PropertySchema.from_row(dict(row)) for row in rows]

    def find(self, scope_type: str, scope_id: str, property_name: str) -> PropertySchema | None:
        row = self._conn().execute(
            """SELECT * FROM property_schemas
               WHERE scope_type = ? AND scope_id = ? AND property_name = ?
               LIMIT 1""",
            (scope_type, scope_id or "", property_name),
        ).fetchone()
        return PropertySchema.from_row(dict(row)) if row else None
```

- [ ] **Step 4: Implement `PropertySchemaService`**

Create `src/services/property_schema.py`:

```python
from dataclasses import dataclass, field
from datetime import datetime
import re

from src.models.property_schema import PropertySchema
from src.repositories.property_schema_repo import PropertySchemaRepository
from src.services.db import Database


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


class PropertySchemaService:
    TYPE_NAMES = {"text", "number", "date", "datetime", "boolean", "url", "node_ref"}

    def __init__(self, db=None, repo: PropertySchemaRepository | None = None):
        self._db = db or Database
        self._repo = repo or PropertySchemaRepository(db=self._db)

    def upsert(self, schema: PropertySchema) -> PropertySchema:
        if schema.property_type not in self.TYPE_NAMES:
            raise ValueError(f"unsupported property type: {schema.property_type}")
        return self._repo.upsert(schema)

    def resolve_schema(self, property_name: str, page_id: str = "", tags: list[str] | None = None, block_id: str = "") -> PropertySchema | None:
        tags = tags or []
        candidates = []
        global_schema = self._repo.find("global", "", property_name)
        if global_schema:
            candidates.append((0, global_schema))
        for tag in tags:
            schema = self._repo.find("tag", tag, property_name)
            if schema:
                candidates.append((1, schema))
        if page_id:
            page_schema = self._repo.find("page", page_id, property_name)
            if page_schema:
                candidates.append((2, page_schema))
        if block_id:
            block_schema = self._repo.find("block", block_id, property_name)
            if block_schema:
                candidates.append((3, block_schema))
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item[0])[-1][1]

    def validate_value(self, property_name: str, value, scope_type: str, scope_id: str = "") -> ValidationResult:
        schema = self._repo.find(scope_type, scope_id or "", property_name)
        if not schema:
            return ValidationResult(valid=True)
        errors = []
        if not self._matches_type(value, schema.property_type):
            errors.append(f"{property_name} must be {schema.property_type}")
        if schema.choices is not None and value not in schema.choices:
            errors.append(f"{property_name} must be one of {schema.choices}")
        return ValidationResult(valid=not errors, errors=errors)

    def _matches_type(self, value, property_type: str) -> bool:
        if property_type == "text":
            return isinstance(value, str)
        if property_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if property_type == "date":
            return isinstance(value, str) and bool(re.match(r"^\d{4}-\d{2}-\d{2}$", value))
        if property_type == "datetime":
            return isinstance(value, str) and bool(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value))
        if property_type == "boolean":
            return isinstance(value, bool)
        if property_type == "url":
            return isinstance(value, str) and value.startswith(("http://", "https://"))
        if property_type == "node_ref":
            return isinstance(value, str) and len(value.strip()) > 0
        return False
```

- [ ] **Step 5: Register repo and service in the container**

Add `property_schema_repo` and `_property_schema` in `src/core/container.py`, initialize the repo in `create_container()`, and add:

```python
@property
def property_schema(self):
    if self._property_schema is None:
        from src.services.property_schema import PropertySchemaService
        self._property_schema = PropertySchemaService(db=self.db, repo=self.property_schema_repo)
    return self._property_schema
```

- [ ] **Step 6: Verify property schema behavior**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_property_schema_validates_supported_types_and_choices tests/test_logseq_graph_phase2.py::test_property_schema_precedence_global_tag_page_block -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/repositories/property_schema_repo.py src/services/property_schema.py src/core/container.py tests/test_logseq_graph_phase2.py
git commit -m "feat(graph): add property schema validation"
```

### Task 5: Effective Property Propagation

**Files:**

- Create: `src/services/effective_properties.py`
- Modify: `src/repositories/block_repo.py`
- Modify: `src/services/file_graph.py`
- Modify: `src/services/query_router.py`
- Modify: `src/core/query_builder.py`
- Test: `tests/test_logseq_graph_phase2.py`

- [ ] **Step 1: Write failing effective-property tests**

Add:

```python
def test_effective_properties_apply_precedence_and_refresh_index():
    from src.models.property_schema import PropertySchema
    from src.services.effective_properties import EffectivePropertyService
    from src.services.property_schema import PropertySchemaService

    _insert_knowledge("page-props", "Page Props", "content", tags=["bug"])
    _insert_block("block-props", "page-props", "Fix login", properties={"status": "done"})

    schemas = PropertySchemaService(db=Database)
    schemas.upsert(PropertySchema(scope_type="global", property_name="status", property_type="text", default_value="draft"))
    schemas.upsert(PropertySchema(scope_type="tag", scope_id="bug", property_name="status", property_type="text", default_value="open"))
    schemas.upsert(PropertySchema(scope_type="page", scope_id="page-props", property_name="owner", property_type="text", default_value="frontend"))

    service = EffectivePropertyService(db=Database)
    props = service.refresh_block("block-props")

    assert props["status"]["value"] == "done"
    assert props["status"]["source_type"] == "block"
    assert props["owner"]["value"] == "frontend"
    assert props["owner"]["source_type"] == "page"

    rows = Database.get_conn().execute(
        "SELECT prop_key, prop_value, source_type, inherited FROM effective_property_index WHERE block_id = ?",
        ("block-props",),
    ).fetchall()
    indexed = {row["prop_key"]: dict(row) for row in rows}
    assert indexed["status"]["prop_value"] == "done"
    assert indexed["owner"]["inherited"] == 1


def test_query_router_uses_effective_inherited_properties():
    from src.models.property_schema import PropertySchema
    from src.services.effective_properties import EffectivePropertyService
    from src.services.property_schema import PropertySchemaService
    from src.services.query_router import QueryRouter

    _insert_knowledge("page-query-props", "Inherited Props", "content", tags=["bug"])
    _insert_block("block-query-props", "page-query-props", "Fix login")
    PropertySchemaService(db=Database).upsert(PropertySchema(
        scope_type="tag",
        scope_id="bug",
        property_name="status",
        property_type="text",
        default_value="open",
    ))
    EffectivePropertyService(db=Database).refresh_page("page-query-props")

    results = QueryRouter(db=Database).search("#bug ::status open", top_k=5)

    assert [result["id"] for result in results] == ["block-query-props"]
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_effective_properties_apply_precedence_and_refresh_index tests/test_logseq_graph_phase2.py::test_query_router_uses_effective_inherited_properties -q
```

Expected: FAIL because `EffectivePropertyService` and effective property search do not exist.

- [ ] **Step 3: Implement `EffectivePropertyService`**

Create `src/services/effective_properties.py`:

```python
import json
from datetime import datetime

from src.services.db import Database
from src.services.property_schema import PropertySchemaService


class EffectivePropertyService:
    def __init__(self, db=None, schema_service: PropertySchemaService | None = None):
        self._db = db or Database
        self._schemas = schema_service or PropertySchemaService(db=self._db)

    def refresh_page(self, page_id: str) -> int:
        rows = self._db.get_conn().execute("SELECT id FROM blocks WHERE page_id = ?", (page_id,)).fetchall()
        count = 0
        for row in rows:
            self.refresh_block(row["id"])
            count += 1
        return count

    def refresh_block(self, block_id: str) -> dict:
        block = self._db.get_block(block_id)
        if not block:
            return {}
        page = self._db.get_knowledge(block["page_id"]) if block.get("page_id") else None
        tags = self._load_tags(page.get("tags") if page else "[]")
        explicit = self._load_props(block.get("properties"))

        effective = {}
        self._apply_scope(effective, "global", "", [], block_id)
        for tag in tags:
            self._apply_scope(effective, "tag", tag, tags, block_id)
        if page:
            self._apply_scope(effective, "page", page["id"], tags, block_id)
        for key, value in explicit.items():
            effective[key] = {
                "value": value,
                "value_type": self._value_type(value),
                "source_type": "block",
                "source_id": block_id,
                "inherited": 0,
            }
        self._write_index(block_id, effective)
        return effective

    def _apply_scope(self, effective: dict, scope_type: str, scope_id: str, tags: list[str], block_id: str):
        schemas = self._schemas._repo.list_for_scope(scope_type, scope_id)
        for schema in schemas:
            if schema.default_value is None:
                continue
            effective[schema.property_name] = {
                "value": schema.default_value,
                "value_type": schema.property_type,
                "source_type": scope_type,
                "source_id": scope_id,
                "inherited": 1,
            }

    def _write_index(self, block_id: str, effective: dict):
        conn = self._db.get_conn()
        conn.execute("DELETE FROM effective_property_index WHERE block_id = ?", (block_id,))
        now = datetime.now().isoformat()
        rows = []
        for key, data in effective.items():
            rows.append({
                "block_id": block_id,
                "prop_key": key,
                "prop_value": self._string_value(data["value"]),
                "value_type": data["value_type"],
                "source_type": data["source_type"],
                "source_id": data["source_id"],
                "inherited": data["inherited"],
                "updated_at": now,
            })
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO effective_property_index
                   (block_id, prop_key, prop_value, value_type, source_type, source_id, inherited, updated_at)
                   VALUES (:block_id, :prop_key, :prop_value, :value_type, :source_type, :source_id, :inherited, :updated_at)""",
                rows,
            )
        conn.commit()

    def _load_props(self, value) -> dict:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}

    def _load_tags(self, value) -> list[str]:
        try:
            parsed = json.loads(value or "[]") if isinstance(value, str) else value
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []

    def _value_type(self, value) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        return "text"

    def _string_value(self, value) -> str:
        return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
```

- [ ] **Step 4: Refresh effective properties after writes**

In `src/repositories/block_repo.py`, after `replace_properties()` commits in `upsert()`, call:

```python
try:
    from src.services.effective_properties import EffectivePropertyService
    EffectivePropertyService(db=self._db).refresh_block(block.id)
except Exception:
    pass
```

In `src/services/file_graph.py`, after successful `create_page()`, `update_page()`, and `sync_page()` cache rebuilds, call:

```python
from src.services.effective_properties import EffectivePropertyService
EffectivePropertyService(db=self._db).refresh_page(page_id)
```

Use the local page id variable already present in each method. If a method catches indexing exceptions, put this refresh inside the same non-fatal indexing block so imports are not blocked by schema mistakes.

- [ ] **Step 5: Query effective properties**

In `src/services/query_router.py`, change the property condition from `block_property_index` only to `effective_property_index`:

```python
conditions.append(
    "EXISTS (SELECT 1 FROM effective_property_index epi "
    "WHERE epi.block_id = b.id AND epi.prop_key = ? AND epi.prop_value = ?)"
)
params.extend([key, value])
```

In `src/core/query_builder.py`, change `HasProperty.to_sql()` to:

```python
return (
    "EXISTS (SELECT 1 FROM effective_property_index epi "
    "JOIN blocks b ON b.id = epi.block_id AND b.page_id = ki.id "
    "WHERE epi.prop_key = ? AND epi.prop_value = ?)",
    [self.key, self.value],
)
```

- [ ] **Step 6: Verify effective property propagation**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_effective_properties_apply_precedence_and_refresh_index tests/test_logseq_graph_phase2.py::test_query_router_uses_effective_inherited_properties -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/services/effective_properties.py src/repositories/block_repo.py src/services/file_graph.py src/services/query_router.py src/core/query_builder.py tests/test_logseq_graph_phase2.py
git commit -m "feat(graph): add effective property inheritance"
```

### Task 6: API Contracts for Phase 2 Graph Capabilities

**Files:**

- Modify: `src/api/routes.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing API tests**

Add these tests under a new `TestPhase2GraphAPI` class in `tests/test_api.py`:

```python
class TestPhase2GraphAPI:
    def test_unified_graph_endpoint_returns_nodes_and_edges(self, api_client):
        from src.services.db import Database
        Database.insert_knowledge({
            "id": "api-page-1",
            "title": "API Page",
            "content": "content",
            "source_type": "manual",
            "source_path": "",
            "file_type": "txt",
            "file_size": 0,
            "content_hash": "",
            "file_created_at": "",
            "file_modified_at": "",
            "tags": '["bug"]',
            "version": 1,
            "created_at": "2026-06-04",
            "updated_at": "2026-06-04",
        })

        resp = api_client.get("/api/graph/unified?include_blocks=false&include_tags=true")

        assert resp.status_code == 200
        ids = {node["id"] for node in resp.json()["nodes"]}
        assert "page:api-page-1" in ids
        assert "tag:bug" in ids

    def test_tag_relation_and_property_schema_endpoints(self, api_client):
        tag_resp = api_client.post("/api/tags/relations", json={"parent_tag": "frontend", "child_tag": "bug"})
        assert tag_resp.status_code == 200
        assert api_client.get("/api/tags/hierarchy/frontend").json()["descendants"] == ["bug"]

        schema_resp = api_client.post("/api/properties/schemas", json={
            "scope_type": "tag",
            "scope_id": "bug",
            "property_name": "status",
            "property_type": "text",
            "choices": ["open", "closed"],
        })
        assert schema_resp.status_code == 200
        schemas = api_client.get("/api/properties/schemas?scope_type=tag&scope_id=bug").json()["schemas"]
        assert schemas[0]["property_name"] == "status"
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
pytest tests/test_api.py::TestPhase2GraphAPI -q
```

Expected: FAIL because routes do not exist.

- [ ] **Step 3: Add request models and endpoints**

In `src/api/routes.py`, add:

```python
class TagRelationReq(BaseModel):
    parent_tag: str
    child_tag: str


class PropertySchemaReq(BaseModel):
    scope_type: str
    scope_id: str = ""
    property_name: str
    property_type: str
    required: int = 0
    default_value: object = None
    choices: list | None = None
    constraints: dict | None = None
```

Add routes:

```python
graph_router = APIRouter(prefix="/graph", tags=["graph"], dependencies=[Depends(_check_auth)])
tags_router = APIRouter(prefix="/tags", tags=["tags"], dependencies=[Depends(_check_auth)])
properties_router = APIRouter(prefix="/properties", tags=["properties"], dependencies=[Depends(_check_auth)])


@graph_router.get("/unified")
def get_unified_graph(
    include_blocks: bool = True,
    include_tags: bool = True,
    container: AppContainer = Depends(get_container),
):
    return container.unified_graph.build(include_blocks=include_blocks, include_tags=include_tags)


@tags_router.post("/relations")
def create_tag_relation(data: TagRelationReq, container: AppContainer = Depends(get_container)):
    container.tag_hierarchy.add_relation(data.parent_tag, data.child_tag)
    return {"parent_tag": data.parent_tag, "child_tag": data.child_tag}


@tags_router.get("/hierarchy/{tag}")
def get_tag_hierarchy(tag: str, container: AppContainer = Depends(get_container)):
    return {
        "tag": tag,
        "ancestors": container.tag_hierarchy.ancestors(tag),
        "descendants": container.tag_hierarchy.descendants(tag),
    }


@properties_router.post("/schemas")
def upsert_property_schema(data: PropertySchemaReq, container: AppContainer = Depends(get_container)):
    from src.models.property_schema import PropertySchema
    schema = container.property_schema.upsert(PropertySchema(**data.model_dump()))
    return schema.to_row()


@properties_router.get("/schemas")
def list_property_schemas(scope_type: str, scope_id: str = "", container: AppContainer = Depends(get_container)):
    return {"schemas": [schema.to_row() for schema in container.property_schema._repo.list_for_scope(scope_type, scope_id)]}


@properties_router.get("/effective/{block_id}")
def get_effective_properties(block_id: str, container: AppContainer = Depends(get_container)):
    return {"block_id": block_id, "properties": container.effective_properties.refresh_block(block_id)}
```

Update the router import in `src/api/__init__.py`:

```python
from src.api.routes import (
    auth_router, kb_router, chat_router, wiki_router, jobs_router, refs_router,
    graph_router, tags_router, properties_router,
)
```

Register the routers in `create_app()` after the existing graph/reference-related routers:

```python
app.include_router(graph_router, prefix="/api")
app.include_router(tags_router, prefix="/api")
app.include_router(properties_router, prefix="/api")
```

- [ ] **Step 4: Register `effective_properties` in the container**

Add:

```python
_effective_properties: Optional[object] = field(default=None, repr=False)

@property
def effective_properties(self):
    if self._effective_properties is None:
        from src.services.effective_properties import EffectivePropertyService
        self._effective_properties = EffectivePropertyService(db=self.db, schema_service=self.property_schema)
    return self._effective_properties
```

- [ ] **Step 5: Verify API contracts**

Run:

```bash
pytest tests/test_api.py::TestPhase2GraphAPI -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/api/routes.py src/api/__init__.py src/core/container.py tests/test_api.py
git commit -m "feat(api): expose phase2 graph APIs"
```

### Task 7: GUI Unified Graph Mode

**Files:**

- Modify: `src/gui/graph_view.py`
- Test: `tests/test_logseq_graph_phase2.py`

- [ ] **Step 1: Write failing pure helper tests for GUI graph mapping**

Add:

```python
def test_gui_unified_node_style_mapping_is_stable():
    from src.gui.graph_view import _style_for_unified_node

    assert _style_for_unified_node({"type": "page"})["shape"] == "ellipse"
    assert _style_for_unified_node({"type": "block"})["shape"] == "rounded_rect"
    assert _style_for_unified_node({"type": "tag"})["shape"] == "diamond"


def test_gui_unified_detail_text_includes_effective_properties():
    from src.gui.graph_view import _unified_node_detail_text

    text = _unified_node_detail_text({
        "id": "block:block-1",
        "type": "block",
        "label": "Fix login",
        "properties": {"status": "open", "owner": "frontend"},
    })

    assert "Fix login" in text
    assert "status" in text
    assert "frontend" in text
```

- [ ] **Step 2: Run the helper tests and confirm they fail**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_gui_unified_node_style_mapping_is_stable tests/test_logseq_graph_phase2.py::test_gui_unified_detail_text_includes_effective_properties -q
```

Expected: FAIL because helper functions do not exist.

- [ ] **Step 3: Add GUI helper functions**

In `src/gui/graph_view.py`, add near the existing color helpers:

```python
def _style_for_unified_node(node: dict) -> dict:
    node_type = node.get("type", "")
    if node_type == "page":
        return {"shape": "ellipse", "color": "#6a9edf"}
    if node_type == "block":
        return {"shape": "rounded_rect", "color": "#6db86d"}
    if node_type == "tag":
        return {"shape": "diamond", "color": "#d8a866"}
    if node_type == "property":
        return {"shape": "hex", "color": "#9a6ad4"}
    return {"shape": "ellipse", "color": "#8e99a5"}


def _unified_node_detail_text(node: dict) -> str:
    lines = [
        node.get("label", node.get("id", "")),
        f"Type: {node.get('type', '')}",
    ]
    props = node.get("properties") or {}
    for key, value in sorted(props.items()):
        lines.append(f"{key}: {value}")
    return "\n".join(lines)
```

- [ ] **Step 4: Add unified mode loading**

Add a graph mode state and toggle in the GraphView toolbar:

```python
self._graph_mode = "legacy"
self.btn_unified_mode = QPushButton("Unified")
self.btn_unified_mode.clicked.connect(self._load_unified_graph)
```

Add a loader method in `GraphView`:

```python
def _load_unified_graph(self):
    from src.services.unified_graph import UnifiedGraphService
    self._graph_mode = "unified"
    payload = UnifiedGraphService(db=Database).build(
        include_blocks=True,
        include_tags=True,
    )
    self.graph_scene.load_unified_payload(payload)
```

Add this method to `GraphScene`:

```python
def load_unified_payload(self, payload: dict) -> None:
    self.clear_graph()
    self._graph_id = "unified"
    node_map: dict[str, GraphNodeItem] = {}

    for idx, node in enumerate(payload.get("nodes", [])):
        style = _style_for_unified_node(node)
        item = GraphNodeItem(
            node_id=node["id"],
            knowledge_id=node.get("source_id") or node["id"],
            knowledge_title=node.get("label") or node["id"],
            file_type=node.get("type", "txt"),
            is_pinned=False,
        )
        item._color_hex = style["color"]
        item._unified_node = node
        item.setToolTip(_unified_node_detail_text(node))
        item.setPos((idx % 10) * 120 - 300, (idx // 10) * 100 - 200)
        node_map[node["id"]] = item
        self.addItem(item)
        self._nodes.append(item)

    for edge in payload.get("edges", []):
        source_node = node_map.get(edge.get("source"))
        target_node = node_map.get(edge.get("target"))
        if source_node is None or target_node is None:
            continue
        edge_item = GraphEdgeItem(
            source_node=source_node,
            target_node=target_node,
            relation_type=edge.get("type", "related"),
            description=str((edge.get("properties") or {}).get("description", "")),
            weight=1.0,
        )
        self.addItem(edge_item)
        self._edges.append(edge_item)

    if self._nodes:
        apply_force_layout(self._nodes, self._edges, iterations=max(30, 200 - len(self._nodes)))
```

Update `_show_node_detail()` in `GraphView` so unified nodes use the payload instead of `Database.get_knowledge()`:

```python
def _show_node_detail(self, node: GraphNodeItem) -> None:
    unified = getattr(node, "_unified_node", None)
    if unified:
        self.detail_title.setText(unified.get("label", unified.get("id", "")))
        self.detail_meta.setText(f"Type: {unified.get('type', '')}")
        self.detail_content.setPlainText(_unified_node_detail_text(unified))
        self._show_detail_panel()
        return

    knowledge = Database.get_knowledge(node.knowledge_id)
    if not knowledge:
        return
    self.detail_title.setText(knowledge.get("title", ""))
    self.detail_meta.setText(f"{knowledge.get('file_type', '')} · {knowledge.get('source_type', '')}")
    content = knowledge.get("content", "")
    self.detail_content.setPlainText(content[:10000] if len(content) > 10000 else content)
    self._show_detail_panel()
```

- [ ] **Step 5: Verify GUI helper tests**

Run:

```bash
pytest tests/test_logseq_graph_phase2.py::test_gui_unified_node_style_mapping_is_stable tests/test_logseq_graph_phase2.py::test_gui_unified_detail_text_includes_effective_properties -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/gui/graph_view.py tests/test_logseq_graph_phase2.py
git commit -m "feat(gui): add unified graph view mode"
```

## Test Plan

- `pytest tests/test_logseq_graph_phase2.py -q`
- `pytest tests/test_api.py::TestPhase2GraphAPI -q`
- `pytest tests/test_core.py tests/test_file_graph.py tests/test_structured_graph_rag_phase1.py -q`
- Full suite after targeted tests pass: `pytest tests -q`

## Acceptance Criteria

- Unified graph payload contains Page, Block, Tag, and relation edges without mutating existing `knowledge_items`, `blocks`, or `entity_refs`.
- Tag hierarchy is a DAG; cycle attempts raise `ValueError`; descendant tag expansion works in structured logic queries.
- Property schemas support `text`, `number`, `date`, `datetime`, `boolean`, `url`, and `node_ref`.
- Effective properties respect precedence: block explicit property > page schema default > tag schema default > global schema default.
- Effective inherited properties are queryable through `QueryRouter` and `query_builder`.
- API exposes unified graph, tag hierarchy, property schemas, and effective properties.
- GUI has a unified graph mode while preserving the current graph view.

## Out of Scope

- Full JSON DSL expansion, natural language to DSL/SQL/Graph Query, and Agentic Router remain Phase 3.
- MCP `dry_run`, operation logs, undo/redo, and write preview remain in the independent operation-safety plan.
- External vector databases and graph databases remain out of scope; this phase continues to use SQLite/sqlite-vec.

## Assumptions

- Phase 1 Structured/Graph RAG foundation is already merged or available in the target branch.
- Existing `knowledge_items.tags` remains the source of page tags; `tag_relations` only models inheritance.
- Property inheritance is computed into `effective_property_index` so structured queries remain fast and do not require recursive SQL at query time.

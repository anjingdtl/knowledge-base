# Logseq Parity Phase 1: Foundation Engineering — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add property type system, bidirectional link auto-discovery, and MCP pretend mode with operation logging to the ShineHeKnowledge knowledge base.

**Architecture:** Three new services (`PropertyValidator`, `LinkDiscoveryService`, `OperationLogService`) integrated into the existing `FileGraphService.sync_page()` pipeline and MCP tool layer. Two new tables (`property_schemas`, `operation_logs`) plus one column addition (`entity_refs.auto_discovered`). All services follow the existing `@dataclass` model pattern, `__init__(db=None)` repository pattern, and lazy `@property` container injection pattern.

**Tech Stack:** Python 3, SQLite, FastMCP, pytest, alembic

---

## File Structure

**New files:**
- `src/models/property_schema.py` — PropertySchema dataclass
- `src/models/operation_log.py` — OperationLog dataclass
- `src/repositories/property_schema_repo.py` — PropertySchemaRepository
- `src/repositories/operation_log_repo.py` — OperationLogRepository
- `src/services/property_schema.py` — PropertyValidator service
- `src/services/link_discovery.py` — LinkDiscoveryService
- `src/services/operation_log.py` — OperationLogService
- `alembic/versions/e001_phase1_foundation.py` — Migration for new tables + column
- `tests/test_property_schema_repo.py` — PropertySchemaRepository tests
- `tests/test_property_validator.py` — PropertyValidator tests
- `tests/test_link_discovery.py` — LinkDiscovery tests
- `tests/test_operation_log.py` — OperationLog + pretend mode tests
- `tests/test_entity_ref_auto_discovered.py` — EntityRef auto_discovered tests
- `tests/test_phase1_integration.py` — Phase 1 integration test

**Modified files:**
- `src/services/db.py` — Add `property_schemas`, `operation_logs` tables to `_SCHEMA`; add `auto_discovered` column to `entity_refs`; add migration in `_migrate()`
- `src/models/block.py` — Add `auto_discovered` field to `EntityRef` dataclass
- `src/repositories/entity_ref_repo.py` — Add `delete_auto_discovered()` method
- `src/core/container.py` — Add lazy properties for 3 new services + 2 new repos
- `src/services/file_graph.py` — Integrate LinkDiscovery in `sync_page()`
- `src/mcp_server.py` — Add `dry_run` param to 7 write tools; add 4 new tools

---

### Task 1: Database Schema — New Tables + Column

**Files:**
- Modify: `src/services/db.py:15-326` (add to `_SCHEMA` string)
- Modify: `src/services/db.py:363-438` (add to `_migrate()`)
- Create: `alembic/versions/e001_phase1_foundation.py`

- [ ] **Step 1: Add `property_schemas` and `operation_logs` tables to `_SCHEMA`**

Insert before the closing `"""` of `_SCHEMA` (after line 325):

```sql
CREATE TABLE IF NOT EXISTS property_schemas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL,
    scope_id TEXT,
    property_name TEXT NOT NULL,
    property_type TEXT NOT NULL,
    required INTEGER DEFAULT 0,
    default_value TEXT,
    choices TEXT,
    constraints TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scope_type, scope_id, property_name)
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    actor TEXT DEFAULT 'mcp',
    params TEXT,
    before_snapshot TEXT,
    after_snapshot TEXT,
    status TEXT DEFAULT 'completed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 2: Add `auto_discovered` column migration to `_migrate()`**

Add to the end of `_migrate()` method:

```python
try:
    cols = [row[1] for row in cls._conn.execute("PRAGMA table_info(entity_refs)").fetchall()]
    if "auto_discovered" not in cols:
        cls._conn.execute("ALTER TABLE entity_refs ADD COLUMN auto_discovered INTEGER DEFAULT 0")
        cls._conn.commit()
except Exception:
    pass
```

- [ ] **Step 3: Create alembic migration file**

```python
"""phase1 foundation - property_schemas, operation_logs, entity_refs.auto_discovered

Revision ID: e001_phase1_foundation
Revises: d001_users_embedding_cache
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa

revision = "e001_phase1_foundation"
down_revision = "d001_users_embedding_cache"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "property_schemas",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("scope_type", sa.Text, nullable=False),
        sa.Column("scope_id", sa.Text, nullable=True),
        sa.Column("property_name", sa.Text, nullable=False),
        sa.Column("property_type", sa.Text, nullable=False),
        sa.Column("required", sa.Integer, server_default="0"),
        sa.Column("default_value", sa.Text, nullable=True),
        sa.Column("choices", sa.Text, nullable=True),
        sa.Column("constraints", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("scope_type", "scope_id", "property_name"),
    )
    op.create_table(
        "operation_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("operation_type", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=False),
        sa.Column("target_id", sa.Text, nullable=True),
        sa.Column("actor", sa.Text, server_default="mcp"),
        sa.Column("params", sa.Text, nullable=True),
        sa.Column("before_snapshot", sa.Text, nullable=True),
        sa.Column("after_snapshot", sa.Text, nullable=True),
        sa.Column("status", sa.Text, server_default="completed"),
        sa.Column("created_at", sa.Text, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.add_column("entity_refs", sa.Column("auto_discovered", sa.Integer, server_default="0"))

def downgrade() -> None:
    op.drop_column("entity_refs", "auto_discovered")
    op.drop_table("operation_logs")
    op.drop_table("property_schemas")
```

- [ ] **Step 4: Verify schema loads correctly**

Run: `python -c "from src.services.db import Database; Database.connect(':memory:'); conn = Database.get_conn(); print([r[1] for r in conn.execute('PRAGMA table_info(property_schemas)').fetchall()]); print([r[1] for r in conn.execute('PRAGMA table_info(operation_logs)').fetchall()]); print('auto_discovered' in [r[1] for r in conn.execute('PRAGMA table_info(entity_refs)').fetchall()])"`

Expected: Column lists for both tables, and `True` for auto_discovered.

- [ ] **Step 5: Commit**

```bash
git add src/services/db.py alembic/versions/e001_phase1_foundation.py
git commit -m "feat(db): add property_schemas, operation_logs tables and entity_refs.auto_discovered"
```

---

### Task 2: PropertySchema Model + Repository

**Files:**
- Create: `src/models/property_schema.py`
- Create: `src/repositories/property_schema_repo.py`
- Create: `tests/test_property_schema_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_property_schema_repo.py
import pytest
from src.models.property_schema import PropertySchema
from src.repositories.property_schema_repo import PropertySchemaRepository


def test_upsert_and_get():
    repo = PropertySchemaRepository()
    schema = PropertySchema(
        scope_type="global",
        scope_id=None,
        property_name="author",
        property_type="text",
        required=True,
    )
    saved = repo.upsert(schema)
    assert saved.id is not None

    result = repo.get_by_id(saved.id)
    assert result.property_name == "author"
    assert result.property_type == "text"
    assert result.required == 1


def test_list_by_scope():
    repo = PropertySchemaRepository()
    repo.upsert(PropertySchema(scope_type="global", scope_id=None, property_name="a", property_type="text"))
    repo.upsert(PropertySchema(scope_type="tag", scope_id="AI", property_name="priority", property_type="number"))
    repo.upsert(PropertySchema(scope_type="tag", scope_id="AI", property_name="status", property_type="text"))

    global_schemas = repo.list_by_scope("global", None)
    assert len(global_schemas) == 1

    tag_schemas = repo.list_by_scope("tag", "AI")
    assert len(tag_schemas) == 2


def test_delete():
    repo = PropertySchemaRepository()
    schema = repo.upsert(PropertySchema(scope_type="global", scope_id=None, property_name="x", property_type="text"))
    deleted = repo.delete(schema.id)
    assert deleted == 1
    assert repo.get_by_id(schema.id) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_property_schema_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.models.property_schema'`

- [ ] **Step 3: Create PropertySchema model**

```python
# src/models/property_schema.py
from dataclasses import dataclass, field
from datetime import datetime
import json


@dataclass
class PropertySchema:
    scope_type: str
    property_name: str
    property_type: str
    id: str = field(default_factory=lambda: "")
    scope_id: str | None = None
    required: int = 0
    default_value: str | None = None
    choices: str | None = None
    constraints: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_row(self) -> dict:
        return {
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "property_name": self.property_name,
            "property_type": self.property_type,
            "required": self.required,
            "default_value": self.default_value,
            "choices": self.choices,
            "constraints": self.constraints,
        }

    @classmethod
    def from_row(cls, row: dict) -> "PropertySchema":
        return cls(
            id=str(row["id"]),
            scope_type=row["scope_type"],
            scope_id=row.get("scope_id"),
            property_name=row["property_name"],
            property_type=row["property_type"],
            required=row.get("required", 0),
            default_value=row.get("default_value"),
            choices=row.get("choices"),
            constraints=row.get("constraints"),
            created_at=row.get("created_at", ""),
        )
```

- [ ] **Step 4: Create PropertySchemaRepository**

```python
# src/repositories/property_schema_repo.py
from src.models.property_schema import PropertySchema


class PropertySchemaRepository:
    def __init__(self, db=None):
        from src.services.db import Database
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    def upsert(self, schema: PropertySchema) -> PropertySchema:
        conn = self._conn()
        cursor = conn.execute(
            """INSERT INTO property_schemas (scope_type, scope_id, property_name, property_type,
               required, default_value, choices, constraints)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(scope_type, scope_id, property_name)
               DO UPDATE SET property_type=excluded.property_type, required=excluded.required,
               default_value=excluded.default_value, choices=excluded.choices, constraints=excluded.constraints""",
            (schema.scope_type, schema.scope_id, schema.property_name, schema.property_type,
             schema.required, schema.default_value, schema.choices, schema.constraints),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM property_schemas WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return PropertySchema.from_row(dict(row))

    def get_by_id(self, schema_id: str) -> PropertySchema | None:
        row = self._conn().execute("SELECT * FROM property_schemas WHERE id = ?", (schema_id,)).fetchone()
        return PropertySchema.from_row(dict(row)) if row else None

    def list_by_scope(self, scope_type: str, scope_id: str | None) -> list[PropertySchema]:
        rows = self._conn().execute(
            "SELECT * FROM property_schemas WHERE scope_type = ? AND scope_id IS ?",
            (scope_type, scope_id),
        ).fetchall()
        return [PropertySchema.from_row(dict(r)) for r in rows]

    def delete(self, schema_id: str) -> int:
        conn = self._conn()
        cursor = conn.execute("DELETE FROM property_schemas WHERE id = ?", (schema_id,))
        conn.commit()
        return cursor.rowcount
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_property_schema_repo.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add src/models/property_schema.py src/repositories/property_schema_repo.py tests/test_property_schema_repo.py
git commit -m "feat: add PropertySchema model and repository"
```

---

### Task 3: PropertyValidator Service

**Files:**
- Create: `src/services/property_schema.py`
- Create: `tests/test_property_validator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_property_validator.py
import pytest
from src.services.property_schema import PropertyValidator


def test_validate_text():
    v = PropertyValidator()
    assert v.validate_value("hello", "text") is True
    assert v.validate_value(123, "text") is False


def test_validate_number():
    v = PropertyValidator()
    assert v.validate_value(42, "number") is True
    assert v.validate_value(3.14, "number") is True
    assert v.validate_value("abc", "number") is False


def test_validate_number_constraints():
    v = PropertyValidator()
    assert v.validate_value(5, "number", constraints={"min": 1, "max": 10}) is True
    assert v.validate_value(15, "number", constraints={"min": 1, "max": 10}) is False


def test_validate_date():
    v = PropertyValidator()
    assert v.validate_value("2026-06-03", "date") is True
    assert v.validate_value("not-a-date", "date") is False


def test_validate_datetime():
    v = PropertyValidator()
    assert v.validate_value("2026-06-03T10:30:00", "datetime") is True
    assert v.validate_value("2026-06-03", "datetime") is False


def test_validate_boolean():
    v = PropertyValidator()
    assert v.validate_value(True, "boolean") is True
    assert v.validate_value(False, "boolean") is True
    assert v.validate_value("true", "boolean") is False


def test_validate_url():
    v = PropertyValidator()
    assert v.validate_value("https://example.com", "url") is True
    assert v.validate_value("not a url", "url") is False


def test_validate_node_ref():
    v = PropertyValidator()
    assert v.validate_value("some-uuid-123", "node_ref") is True
    assert v.validate_value("", "node_ref") is False


def test_validate_choices():
    v = PropertyValidator()
    assert v.validate_value("high", "text", choices=["high", "medium", "low"]) is True
    assert v.validate_value("critical", "text", choices=["high", "medium", "low"]) is False


def test_validate_properties_dict():
    v = PropertyValidator()
    v.define_schema("global", None, "author", "text", required=True)
    v.define_schema("global", None, "priority", "number", constraints={"min": 1, "max": 5})

    result = v.validate_properties("global", None, {"author": "Alice", "priority": 3})
    assert result.valid is True
    assert len(result.errors) == 0


def test_validate_missing_required():
    v = PropertyValidator()
    v.define_schema("global", None, "author", "text", required=True)

    result = v.validate_properties("global", None, {})
    assert result.valid is False
    assert any("author" in e for e in result.errors)


def test_resolve_schema_priority():
    v = PropertyValidator()
    v.define_schema("global", None, "status", "text", default_value="draft")
    v.define_schema("tag", "AI", "status", "text", default_value="review")

    schemas = v.resolve_schema(["AI"], None)
    status = [s for s in schemas if s.property_name == "status"]
    assert len(status) == 1
    assert status[0].default_value == "review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_property_validator.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement PropertyValidator**

```python
# src/services/property_schema.py
from dataclasses import dataclass, field
from datetime import datetime
import re

from src.models.property_schema import PropertySchema
from src.repositories.property_schema_repo import PropertySchemaRepository


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


class PropertyValidator:
    _TYPE_VALIDATORS = {
        "text": lambda v, c: isinstance(v, str),
        "number": lambda v, c: isinstance(v, (int, float)) and not isinstance(v, bool),
        "date": lambda v, c: isinstance(v, str) and bool(re.match(r"^\d{4}-\d{2}-\d{2}$", v)),
        "datetime": lambda v, c: isinstance(v, str) and bool(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", v)),
        "boolean": lambda v, c: isinstance(v, bool),
        "url": lambda v, c: isinstance(v, str) and bool(re.match(r"^https?://", v)),
        "node_ref": lambda v, c: isinstance(v, str) and len(v) > 0,
    }

    def __init__(self, db=None):
        self._repo = PropertySchemaRepository(db=db)

    def define_schema(self, scope_type: str, scope_id: str | None,
                      property_name: str, property_type: str,
                      required: bool = False, default_value=None,
                      choices: list | None = None,
                      constraints: dict | None = None) -> PropertySchema:
        import json
        schema = PropertySchema(
            scope_type=scope_type,
            scope_id=scope_id,
            property_name=property_name,
            property_type=property_type,
            required=1 if required else 0,
            default_value=json.dumps(default_value) if default_value is not None else None,
            choices=json.dumps(choices) if choices else None,
            constraints=json.dumps(constraints) if constraints else None,
        )
        return self._repo.upsert(schema)

    def validate_value(self, value, property_type: str,
                       choices: list | None = None,
                       constraints: dict | None = None) -> bool:
        validator = self._TYPE_VALIDATORS.get(property_type)
        if not validator:
            return False
        if not validator(value, constraints):
            return False
        if choices is not None and value not in choices:
            return False
        if constraints and property_type == "number":
            if "min" in constraints and value < constraints["min"]:
                return False
            if "max" in constraints and value > constraints["max"]:
                return False
        return True

    def validate_properties(self, scope_type: str, scope_id: str | None,
                            properties: dict) -> ValidationResult:
        import json
        schemas = self._repo.list_by_scope(scope_type, scope_id)
        errors = []
        for schema in schemas:
            if schema.required and schema.property_name not in properties:
                errors.append(f"Missing required property: {schema.property_name}")
                continue
            if schema.property_name in properties:
                value = properties[schema.property_name]
                choices = json.loads(schema.choices) if schema.choices else None
                constraints = json.loads(schema.constraints) if schema.constraints else None
                if not self.validate_value(value, schema.property_type, choices, constraints):
                    errors.append(f"Invalid value for {schema.property_name}: expected {schema.property_type}")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def resolve_schema(self, tag_names: list[str],
                       page_id: str | None) -> list[PropertySchema]:
        schemas: dict[str, PropertySchema] = {}
        for s in self._repo.list_by_scope("global", None):
            schemas[s.property_name] = s
        for tag in tag_names:
            for s in self._repo.list_by_scope("tag", tag):
                schemas[s.property_name] = s
        if page_id:
            for s in self._repo.list_by_scope("page", page_id):
                schemas[s.property_name] = s
        return list(schemas.values())

    def get_schema(self, scope_type: str, scope_id: str | None) -> list[PropertySchema]:
        return self._repo.list_by_scope(scope_type, scope_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_property_validator.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/property_schema.py tests/test_property_validator.py
git commit -m "feat: add PropertyValidator service with 7 type validators"
```

---

### Task 4: EntityRef Model Update + Repository Enhancement

**Files:**
- Modify: `src/models/block.py` (EntityRef dataclass)
- Modify: `src/repositories/entity_ref_repo.py` (add auto_discovered support)
- Create: `tests/test_entity_ref_auto_discovered.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_entity_ref_auto_discovered.py
from src.models.block import EntityRef
from src.repositories.entity_ref_repo import EntityRefRepository


def test_entity_ref_auto_discovered_field():
    ref = EntityRef(
        id="test-1",
        source_type="knowledge",
        source_id="k1",
        target_type="knowledge",
        target_id="k2",
        ref_type="link",
        auto_discovered=1,
    )
    assert ref.auto_discovered == 1


def test_delete_auto_discovered():
    repo = EntityRefRepository()
    repo.upsert(EntityRef(id="r1", source_type="knowledge", source_id="k1",
                          target_type="knowledge", target_id="k2", ref_type="link",
                          auto_discovered=1))
    repo.upsert(EntityRef(id="r2", source_type="knowledge", source_id="k1",
                          target_type="knowledge", target_id="k3", ref_type="link",
                          auto_discovered=0))
    deleted = repo.delete_auto_discovered("knowledge", "k1")
    assert deleted == 1
    remaining = repo.list_for_source("knowledge", "k1")
    assert len(remaining) == 1
    assert remaining[0].id == "r2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_ref_auto_discovered.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'auto_discovered'`

- [ ] **Step 3: Update EntityRef model**

In `src/models/block.py`, add `auto_discovered` field to `EntityRef`:

```python
@dataclass
class EntityRef:
    id: str
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    ref_type: str = "mention"
    weight: float = 1.0
    auto_discovered: int = 0
    created_at: str = ""
```

Update `to_row()` to include `"auto_discovered": self.auto_discovered`.
Update `from_row()` to include `auto_discovered=row.get("auto_discovered", 0)`.

- [ ] **Step 4: Add `delete_auto_discovered()` to EntityRefRepository**

```python
def delete_auto_discovered(self, source_type: str, source_id: str) -> int:
    conn = self._conn()
    cursor = conn.execute(
        "DELETE FROM entity_refs WHERE source_type = ? AND source_id = ? AND auto_discovered = 1",
        (source_type, source_id),
    )
    conn.commit()
    return cursor.rowcount
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_entity_ref_auto_discovered.py -v`
Expected: 2 PASS

- [ ] **Step 6: Run existing tests to verify no regressions**

Run: `pytest tests/ -v`
Expected: All existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add src/models/block.py src/repositories/entity_ref_repo.py tests/test_entity_ref_auto_discovered.py
git commit -m "feat: add auto_discovered field to EntityRef and delete_auto_discovered method"
```

---

### Task 5: LinkDiscoveryService

**Files:**
- Create: `src/services/link_discovery.py`
- Create: `tests/test_link_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_link_discovery.py
import pytest
from src.models.knowledge import KnowledgeItem
from src.services.link_discovery import LinkDiscoveryService


def _create_knowledge(title: str, content: str = "", item_id: str = None) -> str:
    from src.services.db import Database
    import uuid
    kid = item_id or str(uuid.uuid4())
    conn = Database.get_conn()
    conn.execute(
        "INSERT INTO knowledge_items (id, title, content, source_type, file_type) VALUES (?, ?, ?, 'manual', 'txt')",
        (kid, title, content),
    )
    conn.commit()
    return kid


def test_scan_wiki_links():
    svc = LinkDiscoveryService()
    k1 = _create_knowledge("Python Basics")
    k2 = _create_knowledge("Machine Learning", content="See [[Python Basics]] for prerequisites.")

    links = svc.scan_content(content="See [[Python Basics]] for prerequisites.",
                             source_id=k2, source_type="knowledge")
    assert len(links) == 1
    assert links[0].target_id == k1
    assert links[0].ref_type == "link"


def test_scan_hashtag():
    svc = LinkDiscoveryService()
    tags = svc.scan_hashtags("This is about #AI and #ML techniques.")
    assert "AI" in tags
    assert "ML" in tags


def test_discover_links_idempotent():
    from src.repositories.entity_ref_repo import EntityRefRepository
    svc = LinkDiscoveryService()
    repo = EntityRefRepository()

    k1 = _create_knowledge("Data Science")
    k2 = _create_knowledge("Statistics", content="Used in [[Data Science]].")

    svc.discover_links(k2)
    refs1 = repo.list_for_source("knowledge", k2)
    auto_refs1 = [r for r in refs1 if r.auto_discovered == 1]
    assert len(auto_refs1) == 1

    svc.discover_links(k2)
    refs2 = repo.list_for_source("knowledge", k2)
    auto_refs2 = [r for r in refs2 if r.auto_discovered == 1]
    assert len(auto_refs2) == 1


def test_discover_preserves_manual_refs():
    from src.repositories.entity_ref_repo import EntityRefRepository
    svc = LinkDiscoveryService()
    repo = EntityRefRepository()

    k1 = _create_knowledge("Algebra")
    k2 = _create_knowledge("Calculus", content="Builds on [[Algebra]].")

    from src.models.block import EntityRef
    repo.upsert(EntityRef(id="manual-1", source_type="knowledge", source_id=k2,
                          target_type="knowledge", target_id="other", ref_type="mention",
                          auto_discovered=0))

    svc.discover_links(k2)
    refs = repo.list_for_source("knowledge", k2)
    assert len(refs) == 2
    manual = [r for r in refs if r.auto_discovered == 0]
    assert len(manual) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_link_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement LinkDiscoveryService**

```python
# src/services/link_discovery.py
import re
import uuid
from dataclasses import dataclass

from src.repositories.entity_ref_repo import EntityRefRepository
from src.models.block import EntityRef


@dataclass
class DiscoveredLink:
    source_id: str
    source_type: str
    target_id: str
    target_type: str
    ref_type: str = "link"


class LinkDiscoveryService:
    _WIKI_LINK_RE = re.compile(r"\[\[([^\]#]+?)(?:#([^\]]+?))?\]\]")
    _HASHTAG_RE = re.compile(r"(?:^|\s)#([A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff-]*)")

    def __init__(self, db=None):
        from src.services.db import Database
        self._db = db or Database
        self._repo = EntityRefRepository(db=self._db)

    def _conn(self):
        return self._db.get_conn()

    def scan_content(self, content: str, source_id: str,
                     source_type: str) -> list[DiscoveredLink]:
        links = []
        for match in self._WIKI_LINK_RE.finditer(content):
            title = match.group(1).strip()
            block_content = match.group(2)

            if block_content:
                row = self._conn().execute(
                    "SELECT id FROM blocks WHERE page_id IN (SELECT id FROM knowledge_items WHERE title = ?) AND content LIKE ?",
                    (title, f"%{block_content}%"),
                ).fetchone()
                if row:
                    links.append(DiscoveredLink(
                        source_id=source_id, source_type=source_type,
                        target_id=str(row["id"]), target_type="block",
                    ))
            else:
                row = self._conn().execute(
                    "SELECT id FROM knowledge_items WHERE title = ?", (title,)
                ).fetchone()
                if row:
                    links.append(DiscoveredLink(
                        source_id=source_id, source_type=source_type,
                        target_id=str(row["id"]), target_type="knowledge",
                    ))
        return links

    def scan_hashtags(self, content: str) -> list[str]:
        return list(set(self._HASHTAG_RE.findall(content)))

    def discover_links(self, knowledge_id: str) -> int:
        conn = self._conn()
        row = conn.execute("SELECT id, content FROM knowledge_items WHERE id = ?", (knowledge_id,)).fetchone()
        if not row:
            return 0

        self._repo.delete_auto_discovered("knowledge", str(knowledge_id))

        block_rows = conn.execute("SELECT id, content FROM blocks WHERE page_id = ?", (knowledge_id,)).fetchall()
        all_content = row["content"] + "\n" + "\n".join(b["content"] for b in block_rows)

        links = self.scan_content(all_content, str(knowledge_id), "knowledge")
        for link in links:
            ref = EntityRef(
                id=str(uuid.uuid4()),
                source_type=link.source_type,
                source_id=link.source_id,
                target_type=link.target_type,
                target_id=link.target_id,
                ref_type=link.ref_type,
                auto_discovered=1,
            )
            self._repo.upsert(ref)

        for block_row in block_rows:
            self._repo.delete_auto_discovered("block", str(block_row["id"]))
            block_links = self.scan_content(block_row["content"], str(block_row["id"]), "block")
            for link in block_links:
                ref = EntityRef(
                    id=str(uuid.uuid4()),
                    source_type=link.source_type,
                    source_id=link.source_id,
                    target_type=link.target_type,
                    target_id=link.target_id,
                    ref_type=link.ref_type,
                    auto_discovered=1,
                )
                self._repo.upsert(ref)

        return len(links) + sum(
            len(self.scan_content(b["content"], str(b["id"]), "block")) for b in block_rows
        )

    def discover_all(self) -> dict:
        conn = self._conn()
        rows = conn.execute("SELECT id FROM knowledge_items").fetchall()
        total_links = 0
        for row in rows:
            total_links += self.discover_links(str(row["id"]))
        return {"processed": len(rows), "links_created": total_links}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_link_discovery.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/link_discovery.py tests/test_link_discovery.py
git commit -m "feat: add LinkDiscoveryService with wiki link and hashtag scanning"
```

---

### Task 6: OperationLog Model + Repository + Service

**Files:**
- Create: `src/models/operation_log.py`
- Create: `src/repositories/operation_log_repo.py`
- Create: `src/services/operation_log.py`
- Create: `tests/test_operation_log.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_operation_log.py
from src.models.operation_log import OperationLog
from src.repositories.operation_log_repo import OperationLogRepository
from src.services.operation_log import OperationLogService


def test_log_and_list():
    repo = OperationLogRepository()
    log = OperationLog(
        operation_type="create",
        target_type="knowledge",
        target_id="k1",
        actor="mcp",
        params='{"title": "test"}',
        after_snapshot='{"id": "k1", "title": "test"}',
        status="completed",
    )
    saved = repo.insert(log)
    assert saved.id is not None

    logs = repo.list_logs(limit=10)
    assert len(logs) == 1
    assert logs[0].operation_type == "create"


def test_list_with_filter():
    repo = OperationLogRepository()
    repo.insert(OperationLog(operation_type="create", target_type="knowledge", status="completed"))
    repo.insert(OperationLog(operation_type="delete", target_type="knowledge", status="completed"))
    repo.insert(OperationLog(operation_type="create", target_type="knowledge", status="pretended"))

    creates = repo.list_logs(operation_type="create")
    assert len(creates) == 2

    pretended = repo.list_logs(status="pretended")
    assert len(pretended) == 1


def test_service_log_operation():
    svc = OperationLogService()
    log_id = svc.log("create", "knowledge", "k1", params={"title": "x"}, status="completed")
    assert log_id is not None

    history = svc.get_history(limit=10)
    assert len(history) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_operation_log.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create OperationLog model**

```python
# src/models/operation_log.py
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class OperationLog:
    operation_type: str
    target_type: str
    id: str = field(default_factory=lambda: "")
    target_id: str | None = None
    actor: str = "mcp"
    params: str | None = None
    before_snapshot: str | None = None
    after_snapshot: str | None = None
    status: str = "completed"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @classmethod
    def from_row(cls, row: dict) -> "OperationLog":
        return cls(
            id=str(row["id"]),
            operation_type=row["operation_type"],
            target_type=row["target_type"],
            target_id=row.get("target_id"),
            actor=row.get("actor", "mcp"),
            params=row.get("params"),
            before_snapshot=row.get("before_snapshot"),
            after_snapshot=row.get("after_snapshot"),
            status=row.get("status", "completed"),
            created_at=row.get("created_at", ""),
        )
```

- [ ] **Step 4: Create OperationLogRepository**

```python
# src/repositories/operation_log_repo.py
from src.models.operation_log import OperationLog


class OperationLogRepository:
    def __init__(self, db=None):
        from src.services.db import Database
        self._db = db or Database

    def _conn(self):
        return self._db.get_conn()

    def insert(self, log: OperationLog) -> OperationLog:
        conn = self._conn()
        cursor = conn.execute(
            """INSERT INTO operation_logs (operation_type, target_type, target_id, actor,
               params, before_snapshot, after_snapshot, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (log.operation_type, log.target_type, log.target_id, log.actor,
             log.params, log.before_snapshot, log.after_snapshot, log.status),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM operation_logs WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return OperationLog.from_row(dict(row))

    def list_logs(self, limit: int = 20, operation_type: str | None = None,
                  status: str | None = None) -> list[OperationLog]:
        query = "SELECT * FROM operation_logs WHERE 1=1"
        params: list = []
        if operation_type:
            query += " AND operation_type = ?"
            params.append(operation_type)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn().execute(query, params).fetchall()
        return [OperationLog.from_row(dict(r)) for r in rows]
```

- [ ] **Step 5: Create OperationLogService**

```python
# src/services/operation_log.py
import json
from src.models.operation_log import OperationLog
from src.repositories.operation_log_repo import OperationLogRepository


class OperationLogService:
    def __init__(self, db=None):
        self._repo = OperationLogRepository(db=db)

    def log(self, operation_type: str, target_type: str, target_id: str | None = None,
            actor: str = "mcp", params: dict | None = None,
            before_snapshot: dict | None = None, after_snapshot: dict | None = None,
            status: str = "completed") -> str:
        log = OperationLog(
            operation_type=operation_type,
            target_type=target_type,
            target_id=target_id,
            actor=actor,
            params=json.dumps(params, ensure_ascii=False) if params else None,
            before_snapshot=json.dumps(before_snapshot, ensure_ascii=False) if before_snapshot else None,
            after_snapshot=json.dumps(after_snapshot, ensure_ascii=False) if after_snapshot else None,
            status=status,
        )
        saved = self._repo.insert(log)
        return saved.id

    def get_history(self, limit: int = 20, operation_type: str | None = None,
                    status: str | None = None) -> list[OperationLog]:
        return self._repo.list_logs(limit=limit, operation_type=operation_type, status=status)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_operation_log.py -v`
Expected: 3 PASS

- [ ] **Step 7: Commit**

```bash
git add src/models/operation_log.py src/repositories/operation_log_repo.py src/services/operation_log.py tests/test_operation_log.py
git commit -m "feat: add OperationLog model, repository, and service"
```

---

### Task 7: Container Integration

**Files:**
- Modify: `src/core/container.py`

- [ ] **Step 1: Add new repository fields and lazy service properties**

Add field declarations after existing repo fields (after line 50):

```python
property_schema_repo: "PropertySchemaRepository" = field(default=None, repr=False)  # noqa: F821
operation_log_repo: "OperationLogRepository" = field(default=None, repr=False)  # noqa: F821
```

Add lazy field declarations after existing lazy fields (after line 67):

```python
_property_validator: Optional[object] = field(default=None, repr=False)
_link_discovery: Optional[object] = field(default=None, repr=False)
_operation_log: Optional[object] = field(default=None, repr=False)
```

Add repo instantiation in `create_container()` after existing repo instantiations (after line 217):

```python
from src.repositories.property_schema_repo import PropertySchemaRepository
from src.repositories.operation_log_repo import OperationLogRepository
container.property_schema_repo = PropertySchemaRepository(db=Database)
container.operation_log_repo = OperationLogRepository(db=Database)
```

Add lazy property methods after existing properties (after line 148):

```python
@property
def property_validator(self):
    if self._property_validator is None:
        from src.services.property_schema import PropertyValidator
        self._property_validator = PropertyValidator(db=self.db)
    return self._property_validator

@property
def link_discovery(self):
    if self._link_discovery is None:
        from src.services.link_discovery import LinkDiscoveryService
        self._link_discovery = LinkDiscoveryService(db=self.db)
    return self._link_discovery

@property
def operation_log(self):
    if self._operation_log is None:
        from src.services.operation_log import OperationLogService
        self._operation_log = OperationLogService(db=self.db)
    return self._operation_log
```

- [ ] **Step 2: Verify container loads without errors**

Run: `python -c "from src.core.container import create_container; c = create_container(); print('property_validator:', type(c.property_validator).__name__); print('link_discovery:', type(c.link_discovery).__name__); print('operation_log:', type(c.operation_log).__name__)"`

Expected: All three service names printed.

- [ ] **Step 3: Commit**

```bash
git add src/core/container.py
git commit -m "feat: wire PropertyValidator, LinkDiscovery, OperationLog into AppContainer"
```

---

### Task 8: Integrate into FileGraphService.sync_page()

**Files:**
- Modify: `src/services/file_graph.py:85-137` (sync_page method)

- [ ] **Step 1: Add LinkDiscovery call to sync_page()**

After line 133 (`self._rebuild_page_cache(page, item)`), add:

```python
try:
    from src.core.container import AppContainer
    if hasattr(self, '_container') and self._container:
        container = self._container
    else:
        container = None
    if container:
        container.link_discovery.discover_links(str(item.id))
except Exception as e:
    logger.warning(f"Link discovery failed for {item.title}: {e}")
```

- [ ] **Step 2: Run existing tests to verify no regressions**

Run: `pytest tests/ -v`
Expected: All existing tests still pass

- [ ] **Step 3: Commit**

```bash
git add src/services/file_graph.py
git commit -m "feat: integrate LinkDiscovery into FileGraphService.sync_page"
```

---

### Task 9: MCP dry_run Parameter + New Tools

**Files:**
- Modify: `src/mcp_server.py` (add dry_run to 7 tools, add 4 new tools)

- [ ] **Step 1: Add dry_run parameter to `create` tool**

In the `create` function (line 162), add `dry_run: bool = False` parameter. Before the actual creation logic, add:

```python
if dry_run:
    container = _get_container()
    would_create = {
        "title": title, "content": content[:200] + "..." if len(content) > 200 else content,
        "tags": tags or [], "file_type": file_type, "source_type": source_type,
    }
    container.operation_log.log("create", "knowledge", actor="mcp",
                                params={"title": title}, after_snapshot=would_create, status="pretended")
    return {"pretend": True, "would_create": would_create}
```

- [ ] **Step 2: Add dry_run to `update`, `delete`, `ingest_file`, `ingest_url`, `save_to_wiki`, `reindex_all`**

Follow the same pattern: add `dry_run: bool = False` parameter, return pretend result + log with status='pretended'.

- [ ] **Step 3: Add `define_property` MCP tool**

```python
@mcp.tool(description="Define a property schema for validation")
@_heartbeat
def define_property(scope_type: str, property_name: str, property_type: str,
                    scope_id: str | None = None, required: bool = False,
                    default_value: str | None = None) -> dict:
    container = _get_container()
    schema = container.property_validator.define_schema(
        scope_type, scope_id, property_name, property_type,
        required=required, default_value=default_value,
    )
    return {"id": schema.id, "property_name": schema.property_name, "property_type": schema.property_type}
```

- [ ] **Step 4: Add `list_properties` MCP tool**

```python
@mcp.tool(description="List property schemas for a scope", annotations={"readOnlyHint": True})
@_heartbeat
def list_properties(scope_type: str, scope_id: str | None = None) -> list[dict]:
    container = _get_container()
    schemas = container.property_validator.get_schema(scope_type, scope_id)
    return [{"id": s.id, "name": s.property_name, "type": s.property_type,
             "required": bool(s.required)} for s in schemas]
```

- [ ] **Step 5: Add `discover_links` MCP tool**

```python
@mcp.tool(description="Discover bidirectional links in knowledge items")
@_heartbeat
def discover_links(item_id: str | None = None) -> dict:
    container = _get_container()
    if item_id:
        count = container.link_discovery.discover_links(item_id)
        return {"processed": 1, "links_created": count}
    else:
        return container.link_discovery.discover_all()
```

- [ ] **Step 6: Add `operation_history` MCP tool**

```python
@mcp.tool(description="Query operation history log", annotations={"readOnlyHint": True})
@_heartbeat
def operation_history(limit: int = 20, operation_type: str | None = None,
                      status: str | None = None) -> list[dict]:
    container = _get_container()
    logs = container.operation_log.get_history(limit=limit, operation_type=operation_type, status=status)
    return [{"id": l.id, "type": l.operation_type, "target": l.target_type,
             "target_id": l.target_id, "status": l.status, "created_at": l.created_at} for l in logs]
```

- [ ] **Step 7: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
git add src/mcp_server.py
git commit -m "feat: add dry_run to MCP write tools, add define_property/list_properties/discover_links/operation_history tools"
```

---

### Task 10: Final Integration Test

**Files:**
- Create: `tests/test_phase1_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_phase1_integration.py
"""Phase 1 integration test: property validation + link discovery + operation logging."""
from src.core.container import create_container


def test_full_pipeline():
    container = create_container()

    container.property_validator.define_schema("global", None, "author", "text", required=True)
    result = container.property_validator.validate_properties("global", None, {"author": "Alice"})
    assert result.valid

    from src.services.db import Database
    conn = Database.get_conn()
    conn.execute("INSERT INTO knowledge_items (id, title, content, source_type, file_type) VALUES ('k1', 'Python', 'Basics', 'manual', 'txt')")
    conn.execute("INSERT INTO knowledge_items (id, title, content, source_type, file_type) VALUES ('k2', 'ML', 'See [[Python]]', 'manual', 'txt')")
    conn.commit()

    stats = container.link_discovery.discover_all()
    assert stats["links_created"] >= 1

    container.operation_log.log("create", "knowledge", "k1", status="completed")
    container.operation_log.log("create", "knowledge", "k2", status="pretended")
    history = container.operation_log.get_history()
    assert len(history) == 2
    pretended = container.operation_log.get_history(status="pretended")
    assert len(pretended) == 1
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/test_phase1_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_phase1_integration.py
git commit -m "test: add Phase 1 integration test"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- Property type system (7 types, Schema definition, validation) — Tasks 2, 3
- Bidirectional link auto-discovery (wiki links, hashtags, idempotent) — Tasks 4, 5
- MCP pretend mode (dry_run param on 7 tools) — Task 9
- Operation logging — Task 6
- New MCP tools (define_property, list_properties, discover_links, operation_history) — Task 9
- Database schema changes — Task 1
- Container wiring — Task 7
- FileGraph integration — Task 8

**2. Placeholder scan:** No TBD/TODO found. All code blocks are complete.

**3. Type consistency:** `PropertySchema`, `PropertyValidator`, `LinkDiscoveryService`, `OperationLogService`, `OperationLog` — names consistent across all tasks.

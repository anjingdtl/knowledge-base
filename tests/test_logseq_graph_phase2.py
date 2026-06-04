"""Phase 2: Logseq Graph — Tag DAG, Property Schema, Effective Properties"""
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


def test_unified_graph_block_limit_only_loads_selected_pages():
    from src.services.unified_graph import UnifiedGraphService

    _insert_knowledge("page-a", "Page A", "content")
    _insert_knowledge("page-b", "Page B", "content")
    _insert_block("block-a1", "page-a", "A1")
    _insert_block("block-a2", "page-a", "A2", order_idx=1)
    _insert_block("block-b1", "page-b", "B1")

    graph = UnifiedGraphService(db=Database).build(
        include_blocks=True,
        include_tags=False,
        page_limit=1,
        block_limit=1,
    )

    node_ids = {node["id"] for node in graph["nodes"]}
    page_ids = {node["source_id"] for node in graph["nodes"] if node["type"] == "page"}
    block_page_ids = {
        node["properties"]["page_id"]
        for node in graph["nodes"]
        if node["type"] == "block"
    }
    assert len([node_id for node_id in node_ids if node_id.startswith("page:")]) == 1
    assert len([node_id for node_id in node_ids if node_id.startswith("block:")]) == 1
    assert block_page_ids <= page_ids


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


def test_tag_inheritance_expands_logic_queries():
    from src.services.effective_properties import EffectivePropertyService
    from src.services.query_router import QueryRouter

    _insert_knowledge("bug-page", "Bug Page", "content", tags=["bug"])
    _insert_block("bug-block", "bug-page", "Fix login", properties={"status": "open"})

    from src.services.tag_hierarchy import TagHierarchyService
    TagHierarchyService(db=Database).add_relation("frontend", "bug")

    EffectivePropertyService(db=Database).refresh_page("bug-page")
    results = QueryRouter(db=Database).search("#frontend ::status open", top_k=5)

    assert [result["id"] for result in results] == ["bug-block"]


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


def test_gui_large_unified_graph_layout_is_bounded():
    from src.gui.graph_view import _layout_iterations_for_node_count

    assert _layout_iterations_for_node_count(20) >= 30
    assert _layout_iterations_for_node_count(1200) == 0


def test_gui_large_outline_uses_partial_render_policy():
    from src.gui.knowledge_view import _outline_render_policy

    assert _outline_render_policy(20).is_partial is False
    assert _outline_render_policy(1200).is_partial is True
    assert _outline_render_policy(1200).limit < 1200

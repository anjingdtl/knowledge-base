"""插件式图后端测试

覆盖:
1. ID 工具函数 (make_node_id / parse_node_id)
2. SQLite 后端基本操作
3. 工厂函数
4. 同步钩子 (enabled/disabled)
5. 迁移工具 (Mock)
6. 服务层集成 (UnifiedGraphService / GraphTraversalService)
"""
import pytest
from unittest.mock import MagicMock, patch

from src.services.graph_backend.base import (
    GraphBackend,
    GraphEdge,
    GraphNode,
    SubgraphResult,
    TraversalResult,
    make_node_id,
    parse_node_id,
)
from src.services.graph_backend.sqlite_backend import SQLiteGraphBackend
from src.services.graph_backend.factory import create_graph_backend
from src.services.graph_backend.sync_hooks import GraphSyncHook
from src.services.db import Database


# ---------------------------------------------------------------------------
# ID 工具函数
# ---------------------------------------------------------------------------

class TestIdUtils:

    def test_make_node_id_page(self):
        assert make_node_id("page", "abc") == "page:abc"

    def test_make_node_id_knowledge_maps_to_page(self):
        assert make_node_id("knowledge", "abc") == "page:abc"

    def test_make_node_id_block(self):
        assert make_node_id("block", "b1") == "block:b1"

    def test_make_node_id_tag(self):
        assert make_node_id("tag", "python") == "tag:python"

    def test_make_node_id_already_prefixed(self):
        assert make_node_id("page", "page:abc") == "page:abc"

    def test_make_node_id_empty(self):
        assert make_node_id("page", "") == ""

    def test_parse_node_id_page(self):
        assert parse_node_id("page:abc") == ("page", "abc")

    def test_parse_node_id_knowledge_maps_to_page(self):
        assert parse_node_id("knowledge:abc") == ("page", "abc")

    def test_parse_node_id_block(self):
        assert parse_node_id("block:b1") == ("block", "b1")

    def test_parse_node_id_no_prefix(self):
        assert parse_node_id("abc") == ("page", "abc")

    def test_roundtrip(self):
        for node_type, source_id in [("page", "x"), ("block", "y"), ("tag", "z")]:
            nid = make_node_id(node_type, source_id)
            parsed_type, parsed_id = parse_node_id(nid)
            assert parsed_type == node_type
            assert parsed_id == source_id


# ---------------------------------------------------------------------------
# GraphNode / GraphEdge 数据模型
# ---------------------------------------------------------------------------

class TestGraphModels:

    def test_graph_node_to_dict(self):
        node = GraphNode(id="page:1", node_type="page", label="Test", source_id="1")
        d = node.to_dict()
        assert d["id"] == "page:1"
        assert d["type"] == "page"
        assert d["label"] == "Test"
        assert d["source_id"] == "1"

    def test_graph_edge_to_dict(self):
        edge = GraphEdge(source="page:1", target="block:2", edge_type="contains")
        d = edge.to_dict()
        assert d["source"] == "page:1"
        assert d["target"] == "block:2"
        assert d["type"] == "contains"


# ---------------------------------------------------------------------------
# SQLite 后端
# ---------------------------------------------------------------------------

def _seed_data():
    """插入测试数据"""
    Database.insert_knowledge({
        "id": "k1", "title": "Page One", "content": "hello",
        "tags": '["python", "test"]', "file_type": "md",
        "source_type": "manual", "source_path": "",
        "content_hash": "h1", "quality": "", "file_size": 0,
        "file_created_at": "", "file_modified_at": "",
        "created_at": "", "updated_at": "", "version": 1,
    })
    Database.insert_knowledge({
        "id": "k2", "title": "Page Two", "content": "world",
        "tags": '["python"]', "file_type": "txt",
        "source_type": "manual", "source_path": "",
        "content_hash": "h2", "quality": "", "file_size": 0,
        "file_created_at": "", "file_modified_at": "",
        "created_at": "", "updated_at": "", "version": 1,
    })
    Database.insert_blocks([
        {"id": "b1", "parent_id": None, "page_id": "k1", "content": "block 1",
         "block_type": "text", "properties": "{}", "order_idx": 0,
         "created_at": "", "updated_at": ""},
        {"id": "b2", "parent_id": "b1", "page_id": "k1", "content": "block 2",
         "block_type": "text", "properties": "{}", "order_idx": 1,
         "created_at": "", "updated_at": ""},
    ])
    conn = Database.get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO entity_refs (id, source_type, source_id, target_type, target_id, ref_type, weight) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ref1", "block", "b1", "knowledge", "k2", "references", 1.0),
    )
    conn.commit()


class TestSQLiteGraphBackend:

    def test_name(self):
        backend = SQLiteGraphBackend(db=Database)
        assert backend.name == "sqlite"

    def test_health_check(self):
        backend = SQLiteGraphBackend(db=Database)
        assert backend.health_check() is True

    def test_get_node_page(self):
        _seed_data()
        backend = SQLiteGraphBackend(db=Database)
        node = backend.get_node("page:k1")
        assert node is not None
        assert node.node_type == "page"
        assert node.label == "Page One"
        assert node.source_id == "k1"

    def test_get_node_block(self):
        _seed_data()
        backend = SQLiteGraphBackend(db=Database)
        node = backend.get_node("block:b1")
        assert node is not None
        assert node.node_type == "block"
        assert node.label == "block 1"

    def test_get_node_not_found(self):
        backend = SQLiteGraphBackend(db=Database)
        node = backend.get_node("page:nonexistent")
        assert node is None

    def test_find_neighbors_page(self):
        _seed_data()
        backend = SQLiteGraphBackend(db=Database)
        neighbors = backend.find_neighbors("page:k1")
        # k1 的 block b1 引用了 k2
        assert len(neighbors) >= 1
        neighbor_ids = {n.id for n, _ in neighbors}
        assert "page:k2" in neighbor_ids

    def test_traverse_basic(self):
        _seed_data()
        backend = SQLiteGraphBackend(db=Database)
        result = backend.traverse(start_ids=["k1"], max_depth=1, max_nodes=50)
        assert isinstance(result, TraversalResult)
        assert len(result.nodes) >= 1
        assert result.truncated is False

    def test_traverse_with_depth(self):
        _seed_data()
        backend = SQLiteGraphBackend(db=Database)
        result = backend.traverse(start_ids=["k1"], max_depth=2, max_nodes=50)
        assert len(result.nodes) >= 1

    def test_load_subgraph(self):
        _seed_data()
        backend = SQLiteGraphBackend(db=Database)
        result = backend.load_subgraph(node_limit=100, edge_limit=1000)
        assert isinstance(result, SubgraphResult)
        assert len(result.nodes) > 0
        # 应有 page 节点
        page_nodes = [n for n in result.nodes if n.get("type") == "page"]
        assert len(page_nodes) >= 2

    def test_load_subgraph_with_type_filter(self):
        _seed_data()
        backend = SQLiteGraphBackend(db=Database)
        result = backend.load_subgraph(node_types=["page"], node_limit=100, edge_limit=1000)
        # 不应包含 block 节点
        block_nodes = [n for n in result.nodes if n.get("type") == "block"]
        assert len(block_nodes) == 0

    def test_get_nodes_by_type(self):
        _seed_data()
        backend = SQLiteGraphBackend(db=Database)
        pages = backend.get_nodes_by_type("page", limit=10)
        assert len(pages) >= 2
        assert all(n.node_type == "page" for n in pages)

    def test_get_edges_by_type(self):
        _seed_data()
        backend = SQLiteGraphBackend(db=Database)
        edges = backend.get_edges_by_type("references", limit=10)
        assert len(edges) >= 1

    def test_stats(self):
        _seed_data()
        backend = SQLiteGraphBackend(db=Database)
        s = backend.stats()
        assert s["backend"] == "sqlite"
        assert s["page_count"] >= 2
        assert s["block_count"] >= 2

    def test_upsert_edge_and_verify(self):
        _seed_data()
        backend = SQLiteGraphBackend(db=Database)
        edge = GraphEdge(
            source="block:b2",
            target="page:k2",
            edge_type="related",
        )
        backend.upsert_edge(edge)
        # 验证写入
        edges = backend.get_edges_by_type("related", limit=10)
        assert len(edges) >= 1


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

class TestFactory:

    def test_default_sqlite(self):
        config = MagicMock()
        config.get.return_value = "sqlite"
        backend = create_graph_backend(config, db=Database)
        assert backend.name == "sqlite"

    def test_unknown_provider_raises(self):
        config = MagicMock()
        config.get.return_value = "redis"
        with pytest.raises(ValueError, match="Unknown graph_backend provider"):
            create_graph_backend(config, db=Database)


# ---------------------------------------------------------------------------
# 同步钩子
# ---------------------------------------------------------------------------

class TestSyncHooks:

    def test_sqlite_backend_disabled(self):
        backend = SQLiteGraphBackend(db=Database)
        hook = GraphSyncHook(backend)
        assert hook.enabled is False

    def test_non_sqlite_backend_enabled(self):
        mock_backend = MagicMock()
        mock_backend.name = "neo4j"
        hook = GraphSyncHook(mock_backend)
        assert hook.enabled is True

    def test_hook_calls_upsert_node(self):
        mock_backend = MagicMock()
        mock_backend.name = "neo4j"
        hook = GraphSyncHook(mock_backend)
        hook.on_page_synced("k1", "Test Page", tags=["python"], file_type="md")
        # 应调用 upsert_node（Page 节点 + Tag 节点）
        assert mock_backend.upsert_node.call_count >= 1

    def test_hook_calls_delete(self):
        mock_backend = MagicMock()
        mock_backend.name = "neo4j"
        hook = GraphSyncHook(mock_backend)
        hook.on_page_deleted("k1")
        mock_backend.delete_node.assert_called_once_with("page:k1")

    def test_hook_calls_batch_blocks(self):
        mock_backend = MagicMock()
        mock_backend.name = "neo4j"
        hook = GraphSyncHook(mock_backend)
        blocks = [
            {"id": "b1", "parent_id": None, "content": "hello", "block_type": "text", "order_idx": 0},
            {"id": "b2", "parent_id": "b1", "content": "world", "block_type": "text", "order_idx": 1},
        ]
        hook.on_blocks_synced("k1", blocks)
        mock_backend.upsert_nodes_batch.assert_called_once()
        mock_backend.upsert_edges_batch.assert_called_once()

    def test_hook_noop_when_disabled(self):
        backend = SQLiteGraphBackend(db=Database)
        hook = GraphSyncHook(backend)
        # 这些调用应该全部是 no-op
        hook.on_page_synced("k1", "Test")
        hook.on_page_deleted("k1")
        hook.on_blocks_synced("k1", [])
        # 不会抛出异常即为通过


# ---------------------------------------------------------------------------
# 迁移工具（Mock 测试）
# ---------------------------------------------------------------------------

class TestMigration:

    def test_migrate_all_mock(self):
        _seed_data()
        from src.services.graph_backend.migration import GraphMigration

        mock_target = MagicMock()
        mock_target.name = "neo4j"
        mock_target.clear = MagicMock()
        mock_target.upsert_nodes_batch = MagicMock()
        mock_target.upsert_edges_batch = MagicMock()
        mock_target.create_indexes = MagicMock()

        migration = GraphMigration(db=Database)
        result = migration.migrate_all(target=mock_target, clear_target=True)

        assert result["pages"] >= 2
        assert result["blocks"] >= 2
        assert result["tags"] >= 1
        assert result["edges"] >= 1
        assert "duration_s" in result
        mock_target.clear.assert_called_once()
        mock_target.create_indexes.assert_called_once()

    def test_migrate_incremental_mock(self):
        _seed_data()
        from src.services.graph_backend.migration import GraphMigration

        mock_target = MagicMock()
        mock_target.name = "neo4j"
        mock_target.upsert_nodes_batch = MagicMock()
        mock_target.upsert_edges_batch = MagicMock()

        migration = GraphMigration(db=Database)
        result = migration.sync_incremental(target=mock_target, since="2020-01-01")

        assert "pages" in result
        assert "blocks" in result


# ---------------------------------------------------------------------------
# 服务层集成
# ---------------------------------------------------------------------------

class TestServiceIntegration:

    def test_unified_graph_with_sqlite_backend(self):
        _seed_data()
        from src.services.unified_graph import UnifiedGraphService
        backend = SQLiteGraphBackend(db=Database)
        service = UnifiedGraphService(db=Database, graph_backend=backend)
        result = service.build()
        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) > 0

    def test_unified_graph_without_backend(self):
        """不传 graph_backend 时应自动创建 SQLite 后端"""
        _seed_data()
        from src.services.unified_graph import UnifiedGraphService
        service = UnifiedGraphService(db=Database)
        result = service.build()
        assert "nodes" in result

    def test_traversal_with_sqlite_backend(self):
        _seed_data()
        from src.services.graph_traversal import GraphTraversalService
        backend = SQLiteGraphBackend(db=Database)
        service = GraphTraversalService(db=Database, graph_backend=backend)
        result = service.traverse(start_ids=["k1"], max_depth=1)
        assert "nodes" in result
        assert "edges" in result

    def test_traversal_without_backend(self):
        """不传 graph_backend 时应自动创建 SQLite 后端"""
        _seed_data()
        from src.services.graph_traversal import GraphTraversalService
        service = GraphTraversalService(db=Database)
        result = service.traverse(start_ids=["k1"], max_depth=1)
        assert "nodes" in result

    def test_source_graph_with_backend(self):
        _seed_data()
        from src.services.source_graph import build_source_graph
        backend = SQLiteGraphBackend(db=Database)
        result = build_source_graph(
            sources=[{"knowledge_id": "k1", "block_id": "b1"}],
            db=Database,
            graph_backend=backend,
        )
        assert "nodes" in result
        assert "edges" in result
        assert "node_count" in result

"""核心模块测试 — DI 容器、事件总线、嵌入缓存、查询构建器、块模型、DSL"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.services.db import Database
from src.utils.config import Config


class TestDualMethodConfig:
    def test_class_mode(self):
        Config.set("test.key", "class_value")
        assert Config.get("test.key") == "class_value"

    def test_instance_mode(self):
        c = Config()
        c.set("test.key2", "instance_value")
        assert c.get("test.key2") == "instance_value"

    def test_class_and_instance_share_default(self):
        c = Config()
        c.set("test.share", "shared")
        assert Config.get("test.share") == "shared"

    def test_save_keeps_secret_when_keyring_write_fails(self, tmp_path, monkeypatch):
        import yaml

        import src.utils.config as config_mod

        class BrokenKeyring:
            class errors:
                class PasswordDeleteError(Exception):
                    pass

            @staticmethod
            def set_password(*args, **kwargs):
                raise RuntimeError("keyring unavailable")

            @staticmethod
            def delete_password(*args, **kwargs):
                raise BrokenKeyring.errors.PasswordDeleteError()

        cfg = Config()
        cfg._data = {"llm": {"api_key": "secret-key", "model": "test"}}
        monkeypatch.setattr(config_mod, "_keyring_available", True)
        monkeypatch.setattr(config_mod, "keyring", BrokenKeyring)

        out = tmp_path / "config.yaml"
        cfg.save(str(out))

        saved = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert saved["llm"]["api_key"] == "secret-key"

    def test_export_secret_env_adds_loaded_secrets_without_overwriting(self):
        cfg = Config()
        cfg._data = {
            "llm": {"api_key": "llm-secret"},
            "embedding": {"api_key": "embedding-secret"},
            "reranker": {"api_key": "reranker-secret"},
        }
        env = {"SHINEHE_LLM_API_KEY": "existing"}

        exported = cfg.export_secret_env(env)

        assert exported["SHINEHE_LLM_API_KEY"] == "existing"
        assert exported["SHINEHE_EMBEDDING_API_KEY"] == "embedding-secret"
        assert exported["SHINEHE_RERANKER_API_KEY"] == "reranker-secret"


class TestDIContainer:
    def test_create_container(self):
        from src.core.container import create_container, shutdown_container
        container = create_container()
        assert container.config is not None
        assert container.db is not None
        assert container.llm is not None
        assert container.embedding is not None
        assert container.vectorstore is not None
        shutdown_container(container)

    def test_container_repositories(self):
        from src.core.container import create_container, shutdown_container
        container = create_container()
        assert container.knowledge_repo is not None
        assert container.conversation_repo is not None
        assert container.wiki_repo is not None
        assert container.graph_repo is not None
        assert container.category_repo is not None
        assert container.job_repo is not None
        shutdown_container(container)

    def test_knowledge_repo_crud(self):
        import uuid

        from src.repositories.knowledge_repo import KnowledgeRepository
        repo = KnowledgeRepository(db=Database)
        uid = str(uuid.uuid4())
        item = {
            "id": uid, "title": "测试CRUD", "content": "内容",
            "source_type": "manual", "source_path": "", "file_type": "txt",
            "file_size": 0, "content_hash": "abc", "file_created_at": "",
            "file_modified_at": "", "tags": '["test"]', "version": 1,
            "created_at": "2026-01-01", "updated_at": "2026-01-01",
        }
        repo.insert(item)
        got = repo.get(uid)
        assert got is not None
        assert got["title"] == "测试CRUD"
        assert repo.get(uid)["content"] == "内容"


class TestEventBus:
    def test_emit_and_subscribe(self):
        from src.core.events import emit, on
        received = []
        on("knowledge.created", lambda sender, **kw: received.append(kw))
        emit("knowledge.created", item_id="abc")
        assert len(received) == 1
        assert received[0]["item_id"] == "abc"

    def test_unknown_event_raises(self):
        from src.core.events import on
        with pytest.raises(KeyError):
            on("nonexistent.event", lambda **kw: None)


class TestEmbeddingCache:
    def test_put_and_get(self):
        from src.core.embedding_cache import EmbeddingCache
        cache = EmbeddingCache(db=Database)
        vec = [0.1, 0.2, 0.3]
        cache.put("hash1", "test-model", vec)
        result = cache.get("hash1", "test-model")
        assert result is not None
        assert len(result) == 3
        assert abs(result[0] - 0.1) < 1e-5

    def test_cache_miss(self):
        from src.core.embedding_cache import EmbeddingCache
        cache = EmbeddingCache(db=Database)
        assert cache.get("nonexistent", "model") is None

    def test_invalidate_model(self):
        from src.core.embedding_cache import EmbeddingCache
        cache = EmbeddingCache(db=Database)
        cache.put("h1", "model-a", [1.0])
        cache.put("h2", "model-b", [2.0])
        cache.invalidate_model("model-a")
        assert cache.get("h1", "model-a") is None
        assert cache.get("h2", "model-b") is not None


class TestQueryBuilder:
    def test_has_tag_query(self):
        from src.core.query_builder import has_tag, query
        # 先插入数据
        Database.insert_knowledge({
            "id": "q1", "title": "Python 入门", "content": "Python 教程",
            "source_type": "manual", "source_path": "", "file_type": "txt",
            "file_size": 0, "content_hash": "", "file_created_at": "",
            "file_modified_at": "", "tags": '["Python","教程"]', "version": 1,
            "created_at": "2026-01-01", "updated_at": "2026-01-01",
        })
        results = query(has_tag("Python"), db=Database)
        assert len(results) == 1
        assert results[0]["title"] == "Python 入门"


class TestBlockModel:
    def test_runtime_schema_creates_block_graph_tables(self):
        rows = Database.get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {row["name"] for row in rows}
        assert {
            "blocks",
            "block_refs",
            "entity_refs",
            "block_property_index",
            "embedding_cache",
            "users",
        }.issubset(names)

    def test_block_crud(self):
        from src.models.block import Block
        b = Block(id="blk-1", page_id="page-1", content="Hello", block_type="text")
        row = b.to_row()
        assert row["id"] == "blk-1"
        assert json.loads(row["properties"]) == {}

        b2 = Block.from_row(row)
        assert b2.id == "blk-1"
        assert b2.content == "Hello"

    def test_entity_ref(self):
        from src.models.block import EntityRef
        ref = EntityRef(
            id="ref-1", source_type="knowledge", source_id="k1",
            target_type="wiki", target_id="w1", ref_type="link",
        )
        row = ref.to_row()
        assert row["source_type"] == "knowledge"
        assert row["ref_type"] == "link"

    def test_block_repository_crud_properties_and_refs(self):
        from src.models.block import Block, EntityRef
        from src.repositories.block_repo import BlockRepository
        from src.repositories.entity_ref_repo import EntityRefRepository

        blocks = BlockRepository(db=Database)
        refs = EntityRefRepository(db=Database)

        blocks.upsert(Block(
            id="chunk-1",
            page_id="knowledge-1",
            content="Block content",
            properties={"priority": "high"},
            order_idx=1,
        ))
        got = blocks.get("chunk-1")
        assert got is not None
        assert got.content == "Block content"
        assert got.properties["priority"] == "high"
        assert blocks.list_by_page("knowledge-1")[0].id == "chunk-1"

        refs.upsert(EntityRef(
            id="ref-1",
            source_type="knowledge",
            source_id="knowledge-1",
            target_type="wiki",
            target_id="wiki-1",
            ref_type="derived_from",
        ))
        assert refs.list_for_source("knowledge", "knowledge-1")[0].target_id == "wiki-1"
        assert refs.list_for_target("wiki", "wiki-1")[0].source_id == "knowledge-1"


class TestDSL:
    def test_parse_simple_tag(self):
        from src.core.query_dsl import TagNode, parse_dsl_query
        ast = parse_dsl_query("[[Python]]")
        assert isinstance(ast, TagNode)
        assert ast.tag == "Python"

    def test_parse_fulltext(self):
        from src.core.query_dsl import FullTextNode, parse_dsl_query
        ast = parse_dsl_query('"async patterns"')
        assert isinstance(ast, FullTextNode)
        assert ast.query == "async patterns"

    def test_parse_and(self):
        from src.core.query_dsl import AndNode, FullTextNode, TagNode, parse_dsl_query
        ast = parse_dsl_query('(and [[Python]] "tutorial")')
        assert isinstance(ast, AndNode)
        assert len(ast.children) == 2
        assert isinstance(ast.children[0], TagNode)
        assert isinstance(ast.children[1], FullTextNode)

    def test_parse_property(self):
        from src.core.query_dsl import PropertyNode, parse_dsl_query
        ast = parse_dsl_query("(property priority high)")
        assert isinstance(ast, PropertyNode)
        assert ast.key == "priority"
        assert ast.value == "high"

    def test_parse_in_query_wrapper(self):
        from src.core.query_dsl import AndNode, parse_dsl_query
        ast = parse_dsl_query('{{query (and [[Python]] "async")}}')
        assert isinstance(ast, AndNode)
        assert len(ast.children) == 2


class TestTransclusion:
    def test_find_references(self):
        from src.core.transclusion import find_embed_references
        text = "参见 {{embed:block:abc123}} 和 {{embed:wiki:概念名}}"
        refs = find_embed_references(text)
        assert len(refs) == 2
        assert refs[0]["type"] == "block"
        assert refs[0]["id"] == "abc123"
        assert refs[1]["type"] == "wiki"

    def test_find_with_display_text(self):
        from src.core.transclusion import find_embed_references
        text = "{{embed:block:abc|显示文字}}"
        refs = find_embed_references(text)
        assert len(refs) == 1
        assert refs[0]["display"] == "显示文字"


class TestPluginSystem:
    def test_plugin_manifest(self):
        from src.core.plugin_system import PluginManifest
        m = PluginManifest(name="test-plugin", version="1.0.0", hooks=["knowledge.created"])
        assert m.name == "test-plugin"
        assert len(m.hooks) == 1

    def test_plugin_manager_list(self):
        from src.core.plugin_system import PluginManager, PluginManifest
        pm = PluginManager()
        pm.register(PluginManifest(name="p1", version="0.1"))
        pm.register(PluginManifest(name="p2", version="0.2"))
        plugins = pm.list_plugins()
        assert len(plugins) == 2
        assert all(not p["loaded"] for p in plugins)


class TestRepositories:
    def test_conversation_repo(self):
        from src.repositories.conversation_repo import ConversationRepository
        repo = ConversationRepository(db=Database)
        cid = repo.insert_conversation({
            "id": "conv-1", "title": "测试对话", "created_at": "2026-01-01",
        })
        assert cid == "conv-1"
        convs = repo.list_conversations()
        assert len(convs) == 1

    def test_job_repo(self):
        from src.repositories.job_repo import JobRepository
        repo = JobRepository(db=Database)
        jid = repo.create_job("test_job", {"key": "val"})
        job = repo.get_job(jid)
        assert job is not None
        assert job["job_type"] == "test_job"
        assert job["status"] == "pending"
        stats = repo.get_job_stats()
        assert "pending" in stats

    def test_category_repo(self):
        from src.repositories.category_repo import CategoryRepository
        repo = CategoryRepository(db=Database)
        repo.insert_category("cat-1", "技术文档")
        cats = repo.get_all_categories()
        assert len(cats) == 1
        assert cats[0]["name"] == "技术文档"

    def test_wiki_repo(self):
        from src.repositories.wiki_repo import WikiRepository
        repo = WikiRepository(db=Database)
        pid = repo.insert_page({
            "id": "wp-1", "title": "Python 基础", "content": "内容",
            "source_ids": "[]", "tags": "[]", "concept_summary": "",
            "status": "draft", "lint_score": 1.0,
            "created_at": "2026-01-01", "updated_at": "2026-01-01",
        })
        assert pid == "wp-1"
        page = repo.get_page("wp-1")
        assert page["title"] == "Python 基础"
        assert repo.count_pages() == 1

    def test_graph_repo(self):
        from src.repositories.graph_repo import GraphRepository
        repo = GraphRepository(db=Database)
        gid = repo.insert_graph("测试图谱")
        graph = repo.get_graph(gid)
        assert graph is not None
        assert graph["name"] == "测试图谱"
        graphs = repo.list_graphs()
        assert len(graphs) == 1


class TestSearchServiceIntegration:
    def test_container_search_service_accessible(self):
        """search_service 属性可访问"""
        from src.core.container import create_container
        container = create_container()
        assert container.search_service is not None
        assert hasattr(container.search_service, 'search')

"""测试配置和 fixtures"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.services.db import Database
from src.utils.config import Config


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    db_path = tmp_path / "test.db"
    Config.load()
    Config.set("storage.data_dir", str(tmp_path))
    Config.set("storage.db_name", "test.db")
    # 重置 Database 实例
    Database._instance = None
    Database.connect(str(db_path))
    if "src.mcp_server" in sys.modules:
        sys.modules["src.mcp_server"]._container = None
    try:
        import src.api.auth as auth_mod
        auth_mod._users_db.clear()
    except Exception:
        pass
    # Reset VectorStore singleton so each test gets fresh state
    from src.services.vectorstore import VectorStore
    VectorStore._instance = None
    VectorStore._initialized = False
    # Reset BlockStore singleton so each test gets fresh state
    from src.services.block_store import BlockStore
    BlockStore._instance = None
    BlockStore._initialized = False
    yield
    if "src.mcp_server" in sys.modules:
        sys.modules["src.mcp_server"]._container = None
    Database.close()
    Database._instance = None
    VectorStore._instance = None
    VectorStore._initialized = False
    BlockStore._instance = None
    BlockStore._initialized = False


@pytest.fixture
def sample_item():
    from src.models.knowledge import KnowledgeItem
    return KnowledgeItem(
        title="测试知识",
        content="这是一段测试内容，用于验证知识库系统的功能。",
        source_type="manual",
        file_type="txt",
        tags=["测试", "单元测试"],
    )


@pytest.fixture
def api_client(setup_db, monkeypatch):
    """创建 API 测试客户端，mock 掉 embedding 和 vectorstore"""
    from fastapi.testclient import TestClient

    # Mock index_knowledge_item to avoid real embedding API calls during tests
    import src.services.indexer as indexer_mod
    from src.api import create_app
    from src.api.auth import register_user
    monkeypatch.setattr(indexer_mod, "index_knowledge_item", lambda item: None)
    monkeypatch.setattr(indexer_mod, "reindex_knowledge_item", lambda *a: None)

    class MockVS:
        def delete_by_knowledge(self, kid): pass
        def add_chunks(self, chunks): pass
    import src.api.routes.knowledge as knowledge_routes
    # VectorStore is accessed via container, not directly imported.
    # Set a placeholder attribute so monkeypatch.setattr doesn't fail.
    if not hasattr(knowledge_routes, "VectorStore"):
        knowledge_routes.VectorStore = None  # type: ignore[attr-defined]
    monkeypatch.setattr(knowledge_routes, "VectorStore", MockVS)

    # Reset rate limiter so test runs don't hit the 10/minute login cap
    import src.api.routes.rate_limiter as rl_mod
    rl_mod.login_limiter._requests.clear()

    register_user("testuser", "testpass123")
    app = create_app()
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "testuser", "password": "testpass123"})
        token = login.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


def _now():
    from datetime import datetime
    return datetime.now().isoformat()


def insert_test_knowledge(title="Test", content="Content", tags=None, item_id=None, **kwargs):
    """插入测试知识条目"""
    import json
    import uuid

    from src.services.db import Database
    kid = item_id or str(uuid.uuid4())
    Database.insert_knowledge({
        "id": kid,
        "title": title,
        "content": content,
        "source_type": kwargs.get("source_type", "manual"),
        "source_path": kwargs.get("source_path", ""),
        "file_type": kwargs.get("file_type", "txt"),
        "file_size": kwargs.get("file_size", 0),
        "content_hash": kwargs.get("content_hash", ""),
        "file_created_at": kwargs.get("file_created_at", ""),
        "file_modified_at": kwargs.get("file_modified_at", ""),
        "tags": json.dumps(tags or [], ensure_ascii=False),
        "version": 1,
        "created_at": _now(),
        "updated_at": _now(),
    })
    return kid


def insert_test_block(page_id, content="Block content", block_type="text",
                      block_id=None, parent_id=None, order_idx=0, properties=None):
    """插入测试 Block"""
    import json
    import uuid

    from src.services.db import Database
    bid = block_id or str(uuid.uuid4())
    Database.insert_blocks([{
        "id": bid,
        "parent_id": parent_id,
        "page_id": page_id,
        "content": content,
        "block_type": block_type,
        "properties": json.dumps(properties or {}, ensure_ascii=False),
        "order_idx": order_idx,
        "created_at": _now(),
        "updated_at": _now(),
    }])
    return bid


def insert_test_wiki_page(title="Wiki Test", content="Wiki content", status="draft",
                          page_id=None, tags=None, concept_summary=""):
    """插入测试 Wiki 页面"""
    import json
    import uuid

    from src.services.db import Database
    pid = page_id or str(uuid.uuid4())
    Database.insert_wiki_page({
        "id": pid,
        "title": title,
        "content": content,
        "source_ids": "[]",
        "tags": json.dumps(tags or [], ensure_ascii=False),
        "concept_summary": concept_summary,
        "status": status,
        "lint_score": 1.0,
        "created_at": _now(),
        "updated_at": _now(),
    })
    return pid

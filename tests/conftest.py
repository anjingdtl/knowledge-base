"""测试配置和 fixtures"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.config import Config
from src.services.db import Database


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    db_path = tmp_path / "test.db"
    Config.load()
    Config.set("storage.data_dir", str(tmp_path))
    Config.set("storage.db_name", "test.db")
    Database._conn = None
    Database._instance = None
    Database.connect(str(db_path))
    try:
        import src.api.auth as auth_mod
        auth_mod._users_db.clear()
    except Exception:
        pass
    # Reset VectorStore singleton so each test gets fresh state
    from src.services.vectorstore import VectorStore
    VectorStore._instance = None
    VectorStore._initialized = False
    yield
    Database.close()
    Database._instance = None
    VectorStore._instance = None
    VectorStore._initialized = False


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
    from src.api import create_app
    from src.api.auth import register_user

    # Mock index_knowledge_item to avoid real embedding API calls during tests
    import src.services.indexer as indexer_mod
    monkeypatch.setattr(indexer_mod, "index_knowledge_item", lambda item: None)
    monkeypatch.setattr(indexer_mod, "reindex_knowledge_item", lambda *a: None)

    class MockVS:
        def delete_by_knowledge(self, kid): pass
        def add_chunks(self, chunks): pass
    import src.api.routes as routes_mod
    monkeypatch.setattr(routes_mod, "VectorStore", MockVS)

    register_user("testuser", "testpass123")
    app = create_app()
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "testuser", "password": "testpass123"})
    token = login.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client

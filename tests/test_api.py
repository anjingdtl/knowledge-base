"""API 接口测试"""
import pytest


class TestAuthAPI:
    def test_register(self, api_client):
        resp = api_client.post("/api/auth/register", json={"username": "newuser", "password": "pass123"})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_login(self, api_client):
        resp = api_client.post("/api/auth/login", json={"username": "testuser", "password": "testpass123"})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_login_wrong_password(self, api_client):
        resp = api_client.post("/api/auth/login", json={"username": "testuser", "password": "wrong"})
        assert resp.status_code == 401

    def test_unauthorized_access(self, api_client):
        client = api_client
        client.headers.pop("Authorization", None)
        resp = client.get("/api/knowledge")
        assert resp.status_code == 401


class TestKnowledgeAPI:
    def test_create(self, api_client):
        resp = api_client.post("/api/knowledge", json={
            "title": "API测试知识",
            "content": "这是通过API创建的知识",
            "tags": ["api", "测试"],
        })
        assert resp.status_code == 201
        assert "id" in resp.json()

    def test_list(self, api_client):
        resp = api_client.get("/api/knowledge")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data

    def test_list_with_pagination(self, api_client):
        for i in range(5):
            api_client.post("/api/knowledge", json={
                "title": f"分页测试 {i}",
                "content": f"内容 {i}",
            })
        resp = api_client.get("/api/knowledge?page=1&page_size=2")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) <= 2

    def test_list_with_tag(self, api_client):
        api_client.post("/api/knowledge", json={
            "title": "标签测试",
            "content": "带标签的内容",
            "tags": ["special"],
        })
        resp = api_client.get("/api/knowledge?tag=special")
        assert resp.status_code == 200

    def test_search(self, api_client):
        api_client.post("/api/knowledge", json={
            "title": "搜索目标",
            "content": "独一无二的内容用于搜索测试",
        })
        resp = api_client.get("/api/knowledge/search?q=搜索目标")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_get_tags(self, api_client):
        api_client.post("/api/knowledge", json={
            "title": "标签",
            "content": "内容",
            "tags": ["tag1", "tag2"],
        })
        resp = api_client.get("/api/knowledge/tags")
        assert resp.status_code == 200
        assert "tag1" in resp.json()["tags"]

    def test_update(self, api_client):
        create = api_client.post("/api/knowledge", json={
            "title": "待更新", "content": "原始内容",
        }).json()
        item_id = create["id"]
        resp = api_client.put(f"/api/knowledge/{item_id}", json={"title": "已更新"})
        assert resp.status_code == 200

    def test_delete(self, api_client):
        create = api_client.post("/api/knowledge", json={
            "title": "待删除", "content": "内容",
        }).json()
        item_id = create["id"]
        resp = api_client.delete(f"/api/knowledge/{item_id}")
        assert resp.status_code == 200

    def test_get_nonexistent(self, api_client):
        resp = api_client.get("/api/knowledge/nonexistent-id")
        assert resp.status_code == 404

    def test_versions(self, api_client):
        create = api_client.post("/api/knowledge", json={
            "title": "版本测试", "content": "v1内容",
        }).json()
        item_id = create["id"]
        api_client.put(f"/api/knowledge/{item_id}", json={"content": "v2内容"})
        resp = api_client.get(f"/api/knowledge/{item_id}/versions")
        assert resp.status_code == 200
        assert len(resp.json()["versions"]) >= 1

    def test_restore_version(self, api_client):
        create = api_client.post("/api/knowledge", json={
            "title": "恢复测试", "content": "原始内容",
        }).json()
        item_id = create["id"]
        api_client.put(f"/api/knowledge/{item_id}", json={"content": "修改内容"})
        resp = api_client.post(f"/api/knowledge/{item_id}/versions/1/restore")
        assert resp.status_code == 200

    def test_export(self, api_client):
        api_client.post("/api/knowledge", json={
            "title": "导出测试", "content": "导出内容",
        })
        resp = api_client.post("/api/knowledge/export", json={})
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1


class TestHealthAPI:
    def test_health(self, api_client):
        resp = api_client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "online"


class TestBlockGraphAPI:
    def test_blocks_endpoint_returns_page_blocks(self, api_client):
        from src.services.db import Database

        item_id = "api-block-page"
        Database.insert_knowledge({
            "id": item_id,
            "title": "Block page",
            "content": "Block source",
            "source_type": "manual",
            "source_path": "",
            "file_type": "txt",
            "file_size": 0,
            "content_hash": "",
            "file_created_at": "",
            "file_modified_at": "",
            "tags": "[]",
            "version": 1,
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        })
        Database.insert_chunks([{
            "id": "api-block-1",
            "knowledge_id": item_id,
            "chunk_index": 0,
            "chunk_text": "First block",
            "created_at": "2026-01-01",
        }])

        resp = api_client.get(f"/api/knowledge/{item_id}/blocks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page_id"] == item_id
        assert data["total"] == 1
        assert data["blocks"][0]["id"] == "api-block-1"

    def test_entity_refs_endpoint_filters_source_and_target(self, api_client):
        from src.models.block import EntityRef
        from src.repositories.entity_ref_repo import EntityRefRepository
        from src.services.db import Database

        repo = EntityRefRepository(db=Database)
        repo.upsert(EntityRef(
            id="api-ref-1",
            source_type="knowledge",
            source_id="k1",
            target_type="wiki",
            target_id="w1",
            ref_type="derived_from",
        ))

        by_source = api_client.get("/api/refs?source_type=knowledge&source_id=k1")
        assert by_source.status_code == 200
        assert by_source.json()["refs"][0]["target_id"] == "w1"

        by_target = api_client.get("/api/refs?target_type=wiki&target_id=w1")
        assert by_target.status_code == 200
        assert by_target.json()["refs"][0]["source_id"] == "k1"


class TestChatSourceContract:
    def test_chat_sources_include_block_contract(self, api_client, monkeypatch):
        import src.api.routes as routes_mod

        class StubRag:
            def query(self, question):
                return {
                    "answer": "answer",
                    "sources": [{
                        "title": "Source title",
                        "knowledge_id": "kid-1",
                        "id": "block-1",
                        "text": "source snippet",
                        "score": 0.75,
                    }],
                }

        monkeypatch.setattr(routes_mod, "RAGService", lambda: StubRag())
        resp = api_client.post("/api/chat/ask", json={"question": "question"})
        assert resp.status_code == 200
        source = resp.json()["sources"][0]
        assert source["knowledge_id"] == "kid-1"
        assert source["block_id"] == "block-1"
        assert source["snippet"] == "source snippet"
        assert source["score"] == 0.75

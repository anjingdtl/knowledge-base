"""API 接口测试"""


class TestAuthAPI:
    def test_register(self, api_client):
        resp = api_client.post("/api/auth/register", json={"username": "newuser", "password": "newpass123"})
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

        api_client.app.state.container._rag_pipeline = StubRag()
        resp = api_client.post("/api/chat/ask", json={"question": "question"})
        assert resp.status_code == 200
        source = resp.json()["sources"][0]
        assert source["knowledge_id"] == "kid-1"
        assert source["block_id"] == "block-1"
        assert source["snippet"] == "source snippet"
        assert source["score"] == 0.75

    def test_chat_ask_returns_and_persists_source_graph(self, api_client):
        import json

        graph = {
            "nodes": [{"id": "block-1", "type": "block", "label": "Hit block"}],
            "edges": [{"source": "page-1", "target": "block-1", "type": "contains"}],
        }

        class StubRag:
            def query(self, question):
                return {
                    "answer": "answer",
                    "sources": [],
                    "source_graph": graph,
                }

        api_client.app.state.container._rag_pipeline = StubRag()
        resp = api_client.post("/api/chat/ask", json={"question": "question"})

        assert resp.status_code == 200
        assert resp.json()["source_graph"] == graph

        conv_id = resp.json()["conversation_id"]
        messages = api_client.get(f"/api/chat/conversations/{conv_id}/messages").json()["messages"]
        assistant_msg = next(msg for msg in messages if msg["role"] == "assistant")
        assert json.loads(assistant_msg["source_graph"]) == graph


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


class TestPhase5WebContracts:
    def test_stats_endpoint_uses_live_container(self, api_client):
        resp = api_client.get("/api/stats")
        assert resp.status_code == 200
        assert "knowledge_count" in resp.json()

    def test_graph_visualize_endpoint(self, api_client):
        resp = api_client.get("/api/graph/visualize?limit=100")
        assert resp.status_code == 200
        assert set(resp.json()) >= {"nodes", "edges"}

    def test_settings_endpoints(self, api_client, monkeypatch):
        monkeypatch.setattr(api_client.app.state.container.config, "save", lambda *args, **kwargs: None)
        current = api_client.get("/api/settings")
        assert current.status_code == 200
        assert set(current.json()) >= {"llm", "embedding", "reranker", "mcp", "graph_backend"}

        saved = api_client.post("/api/settings/mcp", json={
            "write_policy": "preview_only",
            "allow_http_write": False,
        })
        assert saved.status_code == 200

        backup = api_client.post("/api/settings/backup", json={})
        assert backup.status_code == 200

        exported = api_client.get("/api/settings/export")
        assert exported.status_code == 200
        assert "knowledge" in exported.json()

    def test_wiki_create_update_and_workflow_contract(self, api_client):
        created = api_client.post("/api/wiki/pages", json={"title": "Web Wiki", "content": "Draft"})
        assert created.status_code == 201
        page_id = created.json()["id"]

        updated = api_client.put(
            f"/api/wiki/pages/{page_id}",
            json={"title": "Web Wiki Updated", "content": "Updated"},
        )
        assert updated.status_code == 200

        workflow = api_client.post(
            f"/api/wiki/pages/{page_id}/workflow",
            json={"action": "submit_review"},
        )
        assert workflow.status_code == 200

    def test_url_import_creates_job(self, api_client):
        resp = api_client.post("/api/knowledge/import-url", json={"url": "https://example.com"})
        assert resp.status_code == 202
        assert resp.json()["status"] == "pending"

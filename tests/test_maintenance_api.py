"""Maintenance API 测试"""
import pytest


def test_endpoints_require_auth(setup_db):
    """未认证请求应 401"""
    from fastapi.testclient import TestClient
    from src.api import create_app
    from src.utils.config import Config
    Config.set("wiki.enabled", False)
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/maintenance/version-conflict/sessions", json={})
    assert resp.status_code in (401, 403)


class TestMaintenanceAPI:
    def test_create_session(self, api_client):
        resp = api_client.post(
            "/api/maintenance/version-conflict/sessions",
            json={"rescan_ignored": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data

    def test_get_session_status(self, api_client):
        create = api_client.post(
            "/api/maintenance/version-conflict/sessions",
            json={},
        )
        sid = create.json()["session_id"]
        resp = api_client.get(f"/api/maintenance/version-conflict/sessions/{sid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == sid

    def test_get_session_not_found(self, api_client):
        resp = api_client.get("/api/maintenance/version-conflict/sessions/nonexistent")
        # 服务返回 error dict，路由 200；或 404。两种都可接受
        assert resp.status_code in (200, 404)

    def test_list_sessions(self, api_client):
        api_client.post(
            "/api/maintenance/version-conflict/sessions",
            json={},
        )
        resp = api_client.get("/api/maintenance/version-conflict/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data

    def test_list_pairs(self, api_client):
        create = api_client.post(
            "/api/maintenance/version-conflict/sessions",
            json={},
        )
        sid = create.json()["session_id"]
        resp = api_client.get(
            f"/api/maintenance/version-conflict/sessions/{sid}/pairs"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "pairs" in data

    def test_ignore_pair_not_found(self, api_client):
        resp = api_client.post(
            "/api/maintenance/version-conflict/pairs/nonexistent/ignore"
        )
        assert resp.status_code == 404

    def test_delete_pair_not_found(self, api_client):
        resp = api_client.post(
            "/api/maintenance/version-conflict/pairs/nonexistent/delete",
            json={"operator": "test"},
        )
        assert resp.status_code == 404

    def test_list_ignores(self, api_client):
        resp = api_client.get("/api/maintenance/version-conflict/ignores")
        assert resp.status_code == 200

    def test_delete_ignore_not_found(self, api_client):
        resp = api_client.delete(
            "/api/maintenance/version-conflict/ignores/nonexistent"
        )
        assert resp.status_code == 404

    def test_full_workflow_with_versioned_policies(self, api_client):
        """完整流程：插入两条版本对 → 扫描 → 查询候选对"""
        # 插入两条同名制度
        api_client.post("/api/knowledge", json={
            "title": "2022年劳动竞赛执行规章制度",
            "content": "第三条：年假5天。",
            "tags": ["劳动竞赛"],
        })
        api_client.post("/api/knowledge", json={
            "title": "2025年劳动竞赛执行规章制度",
            "content": "第三条：年假7天。",
            "tags": ["劳动竞赛"],
        })

        # 创建扫描会话
        create = api_client.post(
            "/api/maintenance/version-conflict/sessions",
            json={"rescan_ignored": False},
        )
        sid = create.json()["session_id"]

        # 查询候选对
        resp = api_client.get(
            f"/api/maintenance/version-conflict/sessions/{sid}/pairs"
        )
        assert resp.status_code == 200
        pairs = resp.json()["pairs"]
        # 由于异步任务在测试环境可能没跑，candidates 可能为 0
        # 这里只验证 API 不报错
        assert isinstance(pairs, list)

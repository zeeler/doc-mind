"""记忆 API 端点集成测试。"""

import pytest
from fastapi.testclient import TestClient
from server.main import app


@pytest.fixture
def client(tmp_data_dir, monkeypatch):
    monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.services.memory_manager._DEFAULT_DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.routers.documents.DATA_DIR", tmp_data_dir)
    from server.database import reset_engine
    from server.services.memory_manager import MemoryManager
    reset_engine()
    MemoryManager.reset_singleton()
    from server.models.base import Base
    from server.database import get_engine
    Base.metadata.create_all(bind=get_engine())
    return TestClient(app)


class TestMemoriesAPI:
    def test_list_memories(self, client):
        """列出记忆返回正确格式。"""
        resp = client.get("/api/v1/memories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "OK"
        assert isinstance(data["data"], list)

    def test_search_memories_requires_query(self, client):
        """搜索记忆需要 query 参数。"""
        resp = client.get("/api/v1/memories/search")
        assert resp.status_code == 400

    def test_search_memories(self, client):
        """搜索记忆返回结果。"""
        resp = client.get("/api/v1/memories/search?q=测试")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "OK"

    def test_observe_requires_conv_id(self, client):
        """observe 需要 conversation_id。"""
        resp = client.post("/api/v1/memories/observe", json={})
        assert resp.status_code == 400

    def test_consolidate_dry_run(self, client):
        """consolidate dry_run 返回 pairs。"""
        resp = client.post("/api/v1/memories/consolidate", json={"dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "OK"
        assert "pairs" in data["data"]

    def test_consolidate_normal(self, client):
        """consolidate 正常模式返回 merged。"""
        resp = client.post("/api/v1/memories/consolidate", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "OK"
        assert "merged" in data["data"]

    def test_export_get_files(self, client):
        """GET export 返回文件列表。"""
        resp = client.get("/api/v1/memories/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "OK"

    def test_delete_nonexistent_memory(self, client):
        """删除不存在的记忆不报错。"""
        resp = client.delete("/api/v1/memories/nonexistent-id")
        assert resp.status_code == 200

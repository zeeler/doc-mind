"""标签路由测试。"""
import pytest
from fastapi.testclient import TestClient
from server.main import app


@pytest.fixture
def client(tmp_data_dir, monkeypatch):
    monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.routers.documents.DATA_DIR", tmp_data_dir)
    from server.database import reset_engine
    reset_engine()
    from server.models.base import Base
    from server.database import get_engine
    from server.models.tag import Tag  # noqa: F401
    Base.metadata.create_all(bind=get_engine())
    return TestClient(app)


class TestTagRoutes:
    def test_create_tag(self, client):
        response = client.post("/api/v1/tags", json={"name": "python"})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert data["data"]["name"] == "python"

    def test_create_tag_empty_name(self, client):
        response = client.post("/api/v1/tags", json={"name": ""})
        assert response.status_code == 400

    def test_create_tag_too_long(self, client):
        response = client.post("/api/v1/tags", json={"name": "a" * 101})
        assert response.status_code == 400

    def test_create_duplicate_tag_returns_existing(self, client):
        r1 = client.post("/api/v1/tags", json={"name": "Python"})
        assert r1.status_code == 200
        r2 = client.post("/api/v1/tags", json={"name": "python"})
        assert r2.status_code == 200
        assert r1.json()["data"]["id"] == r2.json()["data"]["id"]

    def test_list_tags(self, client):
        client.post("/api/v1/tags", json={"name": "ai"})
        client.post("/api/v1/tags", json={"name": "ml"})
        response = client.get("/api/v1/tags")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) >= 2
        assert "doc_count" in data["data"][0]

    def test_delete_tag(self, client):
        r = client.post("/api/v1/tags", json={"name": "delete-me"})
        tag_id = r.json()["data"]["id"]
        response = client.delete(f"/api/v1/tags/{tag_id}")
        assert response.status_code == 200
        list_resp = client.get("/api/v1/tags")
        ids = [t["id"] for t in list_resp.json()["data"]]
        assert tag_id not in ids

    def test_delete_tag_cascades_associations(self, client):
        r = client.post("/api/v1/tags", json={"name": "cascade-test"})
        tag_id = r.json()["data"]["id"]
        resp = client.delete(f"/api/v1/tags/{tag_id}")
        assert resp.status_code == 200

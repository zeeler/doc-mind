"""集合路由测试。"""
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
    from server.models.collection import Collection  # noqa: F401
    Base.metadata.create_all(bind=get_engine())
    return TestClient(app)


class TestCollectionRoutes:
    def test_create_collection(self, client):
        response = client.post("/api/v1/collections", json={"name": "重要文档"})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert data["data"]["name"] == "重要文档"

    def test_create_collection_empty_name(self, client):
        response = client.post("/api/v1/collections", json={"name": ""})
        assert response.status_code == 400

    def test_list_collections_empty(self, client):
        response = client.get("/api/v1/collections")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["data"], list)

    def test_list_collections(self, client):
        client.post("/api/v1/collections", json={"name": "c1"})
        client.post("/api/v1/collections", json={"name": "c2"})
        response = client.get("/api/v1/collections")
        assert len(response.json()["data"]) >= 2

    def test_update_collection(self, client):
        r = client.post("/api/v1/collections", json={"name": "old-name"})
        cid = r.json()["data"]["id"]
        response = client.put(f"/api/v1/collections/{cid}", json={"name": "new-name", "description": "desc"})
        assert response.status_code == 200
        detail = client.get("/api/v1/collections")
        names = [c["name"] for c in detail.json()["data"]]
        assert "new-name" in names

    def test_update_nonexistent_collection(self, client):
        response = client.put("/api/v1/collections/nonexistent", json={"name": "x"})
        assert response.status_code == 404

    def test_delete_collection(self, client):
        r = client.post("/api/v1/collections", json={"name": "delete-me"})
        cid = r.json()["data"]["id"]
        response = client.delete(f"/api/v1/collections/{cid}")
        assert response.status_code == 200
        list_resp = client.get("/api/v1/collections")
        ids = [c["id"] for c in list_resp.json()["data"]]
        assert cid not in ids

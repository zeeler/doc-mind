"""批量操作测试。"""
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


class TestBatchOperations:
    def test_batch_empty_ids(self, client):
        response = client.post("/api/v1/documents/batch", json={"ids": [], "action": "delete"})
        assert response.status_code == 400

    def test_batch_unknown_action(self, client):
        response = client.post("/api/v1/documents/batch", json={"ids": ["x"], "action": "unknown"})
        assert response.status_code == 400

    def test_batch_delete(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            r1 = client.post("/api/v1/documents/upload", files={"file": ("a.txt", f, "text/plain")})
        with open(sample_txt, "rb") as f2:
            r2 = client.post("/api/v1/documents/upload", files={"file": ("b.txt", f2, "text/plain")})
        ids = [r1.json()["data"]["id"], r2.json()["data"]["id"]]

        response = client.post("/api/v1/documents/batch", json={"ids": ids, "action": "delete"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert all(d["success"] for d in data)

        list_resp = client.get("/api/v1/documents")
        remaining = [d["id"] for d in list_resp.json()["data"]]
        for did in ids:
            assert did not in remaining

    def test_batch_tag(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            r1 = client.post("/api/v1/documents/upload", files={"file": ("a.txt", f, "text/plain")})
        ids = [r1.json()["data"]["id"]]

        response = client.post("/api/v1/documents/batch", json={
            "ids": ids, "action": "tag", "params": {"tags": ["batch-tag"]}
        })
        assert response.status_code == 200
        assert response.json()["data"][0]["success"] is True

    def test_batch_categorize(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            r1 = client.post("/api/v1/documents/upload", files={"file": ("a.txt", f, "text/plain")})
        ids = [r1.json()["data"]["id"]]

        response = client.post("/api/v1/documents/batch", json={
            "ids": ids, "action": "categorize", "params": {"category": "技术"}
        })
        assert response.status_code == 200
        assert response.json()["data"][0]["success"] is True

    def test_batch_partial_failure(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            r1 = client.post("/api/v1/documents/upload", files={"file": ("a.txt", f, "text/plain")})
        ids = [r1.json()["data"]["id"], "nonexistent-id"]

        response = client.post("/api/v1/documents/batch", json={
            "ids": ids, "action": "categorize", "params": {"category": "test"}
        })
        assert response.status_code == 200
        results = response.json()["data"]
        assert results[0]["success"] is True
        assert results[1]["success"] is False

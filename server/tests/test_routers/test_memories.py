# server/tests/test_routers/test_memories.py
import pytest
from fastapi.testclient import TestClient
from server.main import app


@pytest.fixture
def client(tmp_data_dir, monkeypatch):
    monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.services.memory.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.routers.documents.DATA_DIR", tmp_data_dir)
    from server.database import reset_engine
    reset_engine()
    from server.models.base import Base
    from server.database import get_engine
    Base.metadata.create_all(bind=get_engine())
    return TestClient(app)


class TestMemoryRoutes:
    def test_list_memories_empty(self, client):
        response = client.get("/api/v1/memories")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert isinstance(data["data"], list)

    def test_search_without_query(self, client):
        response = client.get("/api/v1/memories/search")
        assert response.status_code == 400

    def test_delete_nonexistent(self, client):
        response = client.delete("/api/v1/memories/nonexistent")
        assert response.status_code == 200

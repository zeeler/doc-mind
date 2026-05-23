# server/tests/test_routers/test_conversations.py
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
    Base.metadata.create_all(bind=get_engine())
    return TestClient(app)


class TestConversationRoutes:
    def test_create_conversation(self, client):
        response = client.post("/api/v1/conversations", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert "id" in data["data"]

    def test_list_conversations(self, client):
        client.post("/api/v1/conversations", json={})
        response = client.get("/api/v1/conversations")
        data = response.json()
        assert len(data["data"]) >= 1

    def test_get_conversation_with_messages(self, client):
        create_resp = client.post("/api/v1/conversations", json={})
        conv_id = create_resp.json()["data"]["id"]
        response = client.get(f"/api/v1/conversations/{conv_id}")
        data = response.json()
        assert data["data"]["id"] == conv_id
        assert "messages" in data["data"]

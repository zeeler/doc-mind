# server/tests/test_routers/test_config.py
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


class TestConfigRoutes:
    def test_get_config(self, client):
        response = client.get("/api/v1/config")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert "llm_provider" in data["data"]

    def test_update_config(self, client):
        response = client.put("/api/v1/config", json={"llm_provider": "openai"})
        assert response.status_code == 200
        get_resp = client.get("/api/v1/config")
        assert get_resp.json()["data"]["llm_provider"] == "openai"

    def test_get_models(self, client):
        response = client.get("/api/v1/config/models")
        assert response.status_code == 200
        data = response.json()
        assert "models" in data["data"]

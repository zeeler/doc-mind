# server/tests/test_routers/test_jobs.py
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


class TestJobRoutes:
    def test_job_stats_empty(self, client):
        response = client.get("/api/v1/jobs/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert "quick_scan" in data["data"]
        assert "full_index" in data["data"]
        assert data["data"]["quick_scan"]["pending"] == 0

    def test_list_jobs_empty(self, client):
        response = client.get("/api/v1/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["data"] == []

    def test_retry_nonexistent_job(self, client):
        response = client.post("/api/v1/jobs/nonexistent/retry")
        assert response.status_code == 404

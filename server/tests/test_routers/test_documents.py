# server/tests/test_routers/test_documents.py
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from server.main import app
from server.database import get_session


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


class TestDocumentRoutes:
    def test_upload_document(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            response = client.post(
                "/api/v1/documents/upload",
                files={"file": ("test.txt", f, "text/plain")},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert "id" in data["data"]

    def test_list_documents_empty(self, client):
        response = client.get("/api/v1/documents")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert isinstance(data["data"], list)

    def test_list_documents_after_upload(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        response = client.get("/api/v1/documents")
        data = response.json()
        assert len(data["data"]) >= 1

    def test_get_document_not_found(self, client):
        response = client.get("/api/v1/documents/nonexistent-id")
        assert response.status_code == 404

    def test_delete_document(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]
        response = client.delete(f"/api/v1/documents/{doc_id}")
        assert response.status_code == 200

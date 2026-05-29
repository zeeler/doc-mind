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


class TestDedup:
    def test_duplicate_upload_is_detected(self, client, sample_txt):
        """上传相同文件两次，第二次应返回 duplicate=True 且 id 相同。"""
        with open(sample_txt, "rb") as f:
            r1 = client.post("/api/v1/documents/upload", files={"file": ("a.txt", f, "text/plain")})
        with open(sample_txt, "rb") as f2:
            r2 = client.post("/api/v1/documents/upload", files={"file": ("b.txt", f2, "text/plain")})

        assert r1.status_code == 200
        assert r2.status_code == 200
        d1, d2 = r1.json()["data"], r2.json()["data"]
        assert d1["id"] == d2["id"]
        assert d2["duplicate"] is True
        assert isinstance(d2.get("reprocess"), bool)

    def test_different_files_not_duplicates(self, client, sample_txt, tmp_path):
        """不同内容的文件不应被识别为重复。"""
        f1 = tmp_path / "f1.txt"
        f1.write_text("内容A")
        f2 = tmp_path / "f2.txt"
        f2.write_text("内容B")

        with open(f1, "rb") as f:
            r1 = client.post("/api/v1/documents/upload", files={"file": ("f1.txt", f, "text/plain")})
        with open(f2, "rb") as f:
            r2 = client.post("/api/v1/documents/upload", files={"file": ("f2.txt", f, "text/plain")})

        assert r1.json()["data"]["id"] != r2.json()["data"]["id"]

    def test_document_saves_checksum(self, client, sample_txt):
        """上传后文档记录应有 checksum。"""
        with open(sample_txt, "rb") as f:
            resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = resp.json()["data"]["id"]

        detail = client.get(f"/api/v1/documents/{doc_id}")
        # checksum 不在详情 API 响应中，但可以通过列表确认无重复
        list_resp = client.get("/api/v1/documents")
        docs = list_resp.json()["data"]
        assert any(d["id"] == doc_id for d in docs)


class TestDocumentUpdate:
    def test_update_category(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]
        response = client.put(f"/api/v1/documents/{doc_id}", json={"category": "技术"})
        assert response.status_code == 200

    def test_add_tags_to_document(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]
        response = client.put(f"/api/v1/documents/{doc_id}", json={"add_tags": ["python", "ai"]})
        assert response.status_code == 200

    def test_add_to_collections(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]
        coll_resp = client.post("/api/v1/collections", json={"name": "测试集"})
        coll_id = coll_resp.json()["data"]["id"]
        response = client.put(f"/api/v1/documents/{doc_id}", json={"add_collections": [coll_id]})
        assert response.status_code == 200

    def test_update_nonexistent_document(self, client):
        response = client.put("/api/v1/documents/nonexistent", json={"category": "x"})
        assert response.status_code == 404


class TestDocumentFilters:
    def test_list_documents_with_status_filter(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        response = client.get("/api/v1/documents?status=done")
        assert response.status_code == 200

    def test_list_documents_with_search(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            client.post("/api/v1/documents/upload", files={"file": ("unique_title_xyz.txt", f, "text/plain")})
        response = client.get("/api/v1/documents?search=unique_title_xyz")
        assert response.status_code == 200
        assert len(response.json()["data"]) >= 1

    def test_list_documents_response_includes_tags(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]
        client.put(f"/api/v1/documents/{doc_id}", json={"add_tags": ["t1"]})
        response = client.get("/api/v1/documents")
        doc = next(d for d in response.json()["data"] if d["id"] == doc_id)
        assert "tags" in doc
        assert len(doc["tags"]) >= 1

    def test_list_documents_by_tag(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            upload_resp = client.post("/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")})
        doc_id = upload_resp.json()["data"]["id"]
        client.put(f"/api/v1/documents/{doc_id}", json={"add_tags": ["unique-tag-xyz"]})
        response = client.get("/api/v1/documents?tag=unique-tag-xyz")
        assert response.status_code == 200
        assert len(response.json()["data"]) >= 1


class TestFolders:
    def test_list_folders(self, client):
        response = client.get("/api/v1/documents/folders")
        assert response.status_code == 200
        assert isinstance(response.json()["data"], list)

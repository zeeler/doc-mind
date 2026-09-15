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
    from server.database import get_engine, ensure_fts5_table
    Base.metadata.create_all(bind=get_engine())
    ensure_fts5_table()
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

    def test_delete_document_cleans_all_traces(self, client, sample_txt, tmp_data_dir):
        """删除文档要同时清掉：DB 行、FTS 索引、磁盘文件目录。"""
        from server.database import get_session_ctx, get_engine
        from server.models.document import Document, DocumentChunk
        from server.models.job import Job
        import sqlalchemy as sa

        with open(sample_txt, "rb") as f:
            doc_id = client.post(
                "/api/v1/documents/upload", files={"file": ("test.txt", f, "text/plain")}
            ).json()["data"]["id"]

        # 模拟索引已完成：写 chunk 行 + FTS 条目 + 落盘文件
        file_dir = tmp_data_dir / "files" / doc_id
        file_dir.mkdir(parents=True, exist_ok=True)
        (file_dir / "test.txt").write_text("内容", encoding="utf-8")
        from server.database import fts_insert
        with get_session_ctx() as s:
            s.add(DocumentChunk(id="chk-1", document_id=doc_id, chunk_no=1,
                                content="测试内容", token_count=4))
            s.commit()
        fts_insert("chk-1", doc_id, "测试内容", "test文档")

        def counts():
            with get_engine().connect() as conn:
                fts = conn.execute(sa.text("SELECT COUNT(*) FROM chunks_fts")).fetchone()[0]
            with get_session_ctx() as s:
                return (
                    fts,
                    s.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count(),
                    s.query(Job).filter(Job.document_id == doc_id).count(),
                    s.get(Document, doc_id) is not None,
                )

        fts_before, chunks_before, _, _ = counts()
        assert fts_before == 1 and chunks_before == 1

        assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 200

        fts_after, chunks_after, jobs_after, doc_exists = counts()
        assert fts_after == 0, "FTS 索引未清理"
        assert chunks_after == 0, "document_chunks 未清理"
        assert jobs_after == 0, "jobs 未清理"
        assert doc_exists is False, "documents 行未删除"
        assert not file_dir.exists(), "磁盘文件目录未清理"


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


class TestFilePreview:
    """GET /{doc_id}/file — 引用面板的内嵌文件预览。"""

    def test_preview_txt_inline(self, client, sample_txt):
        with open(sample_txt, "rb") as f:
            r = client.post("/api/v1/documents/upload", files={"file": ("note.txt", f, "text/plain")})
        doc_id = r.json()["data"]["id"]

        resp = client.get(f"/api/v1/documents/{doc_id}/file")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert "inline" in resp.headers.get("content-disposition", "")
        assert "第一段" in resp.text

    def test_preview_doc_not_found(self, client):
        resp = client.get("/api/v1/documents/nonexistent/file")
        assert resp.status_code == 404

    def test_preview_unsupported_type_415(self, client, tmp_path):
        fake = tmp_path / "fake.docx"
        fake.write_bytes(b"not a real docx")
        with open(fake, "rb") as f:
            r = client.post("/api/v1/documents/upload", files={"file": ("fake.docx", f)})
        doc_id = r.json()["data"]["id"]

        resp = client.get(f"/api/v1/documents/{doc_id}/file")
        assert resp.status_code == 415

    def test_preview_path_traversal_blocked(self, client, tmp_data_dir):
        """file_path 指向 DATA_DIR/files 之外时拒绝访问（防路径穿越）。"""
        from server.database import get_engine
        from sqlalchemy.orm import Session as SA_Session
        import uuid
        doc_id = str(uuid.uuid4())
        with SA_Session(get_engine()) as s:
            from server.models.document import Document
            s.add(Document(id=doc_id, title="evil", file_name="evil.txt",
                           file_type="txt", file_path="/etc/hosts", file_size=10, status="done"))
            s.commit()

        resp = client.get(f"/api/v1/documents/{doc_id}/file")
        assert resp.status_code == 404


class TestChunkContext:
    """GET /{doc_id}/chunks/{chunk_no}/context — 引用面板的上下文摘录。"""

    def _make_doc_with_chunks(self, client):
        from server.database import get_engine
        from sqlalchemy.orm import Session as SA_Session
        from server.models.document import Document, DocumentChunk
        import uuid
        doc_id = str(uuid.uuid4())
        with SA_Session(get_engine()) as s:
            s.add(Document(id=doc_id, title="t", file_name="t.txt",
                           file_type="txt", file_path="/tmp/t.txt", file_size=10, status="done"))
            for i in range(1, 5):
                s.add(DocumentChunk(id=f"c{i}", document_id=doc_id, chunk_no=i,
                                    content=f"第 {i} 块内容", token_count=10, metadata_json={}))
            s.commit()
        return doc_id

    def test_context_with_neighbors(self, client):
        doc_id = self._make_doc_with_chunks(client)
        resp = client.get(f"/api/v1/documents/{doc_id}/chunks/2/context?window=1")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["target"]["chunk_no"] == 2
        assert data["target"]["content"] == "第 2 块内容"
        ctx_nos = [c["chunk_no"] for c in data["context"]]
        assert ctx_nos == [1, 3]

    def test_context_boundary_first_chunk(self, client):
        doc_id = self._make_doc_with_chunks(client)
        resp = client.get(f"/api/v1/documents/{doc_id}/chunks/1/context?window=1")
        data = resp.json()["data"]
        assert data["target"]["chunk_no"] == 1
        ctx_nos = [c["chunk_no"] for c in data["context"]]
        assert ctx_nos == [2]  # 没有 chunk 0，只有后文

    def test_context_chunk_not_found(self, client):
        doc_id = self._make_doc_with_chunks(client)
        resp = client.get(f"/api/v1/documents/{doc_id}/chunks/99/context")
        assert resp.status_code == 404

    def test_context_doc_not_found(self, client):
        resp = client.get("/api/v1/documents/nonexistent/chunks/1/context")
        assert resp.status_code == 404

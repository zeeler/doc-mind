# server/tests/test_document_model.py
import pytest
from sqlalchemy import inspect
from server.database import get_engine, reset_engine
from server.models.base import Base
from server.models.document import Document, DocumentChunk


class TestDocumentModel:
    def test_document_table_exists(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        reset_engine()
        Base.metadata.create_all(bind=get_engine())
        insp = inspect(get_engine())
        columns = {c["name"] for c in insp.get_columns("documents")}
        assert "id" in columns
        assert "title" in columns
        assert "file_name" in columns
        assert "file_type" in columns
        assert "file_path" in columns
        assert "file_size" in columns
        assert "status" in columns
        assert "chunk_count" in columns
        assert "created_at" in columns
        assert "updated_at" in columns

    def test_document_default_status_is_pending(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        reset_engine()
        Base.metadata.create_all(bind=get_engine())
        doc = Document(title="test", file_name="test.pdf", file_type="pdf", file_path="/tmp/test.pdf", file_size=1024)
        assert doc.status == "pending"
        assert doc.chunk_count == 0

    def test_document_chunk_table_exists(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        reset_engine()
        Base.metadata.create_all(bind=get_engine())
        insp = inspect(get_engine())
        columns = {c["name"] for c in insp.get_columns("document_chunks")}
        assert "id" in columns
        assert "document_id" in columns
        assert "chunk_no" in columns
        assert "content" in columns
        assert "token_count" in columns
        assert "metadata_json" in columns

import pytest
from unittest.mock import MagicMock, patch
from server.services.pipeline import process_document


class TestPipeline:
    @patch("server.services.pipeline.VectorStore")
    @patch("server.services.pipeline.Embedder")
    def test_process_document(self, MockEmbedder, MockStore, tmp_data_dir, monkeypatch, sample_txt):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        monkeypatch.setattr("server.services.pipeline.DATA_DIR", tmp_data_dir)
        from server.database import reset_engine, get_session
        from server.models.base import Base
        from server.models.document import Document
        from server.database import get_engine
        reset_engine()
        Base.metadata.create_all(bind=get_engine())
        from server.database import ensure_fts5_table
        ensure_fts5_table()

        doc = Document(
            id="test-doc-1",
            title="测试",
            file_name="test.txt",
            file_type="txt",
            file_path=str(sample_txt),
            file_size=100,
            status="pending",
        )
        with next(get_session()) as s:
            s.add(doc)
            s.commit()

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [[0.1, 0.2], [0.3, 0.4]]
        MockEmbedder.return_value = mock_embedder

        mock_store = MagicMock()
        MockStore.return_value = mock_store

        process_document("test-doc-1", config={})

        with next(get_session()) as s:
            updated = s.get(Document, "test-doc-1")
            assert updated is not None

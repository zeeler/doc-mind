import pytest
from unittest.mock import MagicMock, patch
from server.services.pipeline import index_document


class TestPipeline:
    @patch("server.services.pipeline.VectorStore")
    @patch("server.services.pipeline.Embedder")
    def test_index_document(self, MockEmbedder, MockStore, tmp_data_dir, monkeypatch, sample_txt):
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

        sample_text = sample_txt.read_text(encoding="utf-8")
        index_document("test-doc-1", sample_text, config={})

        with next(get_session()) as s:
            updated = s.get(Document, "test-doc-1")
            assert updated is not None

    @patch("server.services.pipeline.VectorStore")
    @patch("server.services.pipeline.Embedder")
    def test_external_embedding_fallback_no_ghost_chunks(self, MockEmbedder, MockStore, tmp_data_dir, monkeypatch, sample_txt):
        """外部 embedding 中途失败回退内置时，不应产生重复的幽灵 chunk 行（旧 bug: 2N 行）。"""
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        monkeypatch.setattr("server.services.pipeline.DATA_DIR", tmp_data_dir)
        from server.database import reset_engine, get_session, ensure_fts5_table
        from server.models.base import Base
        from server.models.document import Document, DocumentChunk
        from server.database import get_engine
        reset_engine()
        Base.metadata.create_all(bind=get_engine())
        ensure_fts5_table()

        doc = Document(
            id="ghost-doc",
            title="幽灵测试",
            file_name="g.txt",
            file_type="txt",
            file_path=str(sample_txt),
            file_size=100,
            status="pending",
        )
        with next(get_session()) as s:
            s.add(doc)
            s.commit()

        # 探测 embed 成功，但正式索引第一批就失败 → 触发回退内置 embedding
        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = [[[0.1, 0.2, 0.3]], Exception("embedding 服务抖动")]
        MockEmbedder.return_value = mock_embedder
        MockStore.return_value = MagicMock()

        text = "第一段内容，用于触发多次切块。" * 20
        config = {"embedding_enabled": "true", "chunk_size": "60", "chunk_overlap": "10"}
        index_document("ghost-doc", text, config)

        with next(get_session()) as s:
            chunks = s.query(DocumentChunk).filter(DocumentChunk.document_id == "ghost-doc").all()
            updated = s.get(Document, "ghost-doc")
            assert len(chunks) > 0
            # chunk_count 与实际行数一致（旧 bug 会残留第一次尝试的 pending 行，变成 2N）
            assert updated.chunk_count == len(chunks)
            ids = [c.id for c in chunks]
            assert len(ids) == len(set(ids))

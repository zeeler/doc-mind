import pytest
from unittest.mock import MagicMock, patch
from server.services.retriever import Retriever


class TestRetriever:
    @patch("server.services.retriever.Embedder")
    def test_retrieve_returns_chunks_with_scores(self, MockEmbedder):
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [[0.1, 0.2]]
        MockEmbedder.return_value = mock_embedder

        mock_store = MagicMock()
        mock_store.search.return_value = [
            {"id": "c1", "content": "相关段落A", "metadata": {"document_id": "d1", "title": "文档A", "file_name": "a.pdf"}, "score": 0.9},
            {"id": "c2", "content": "相关段落B", "metadata": {"document_id": "d1", "title": "文档A", "file_name": "a.pdf"}, "score": 0.7},
        ]

        retriever = Retriever(vector_store=mock_store, config={"retrieval_top_k": "3"})
        results = retriever.retrieve("测试问题")

        assert len(results) == 2
        assert results[0]["score"] >= results[1]["score"]
        assert results[0]["document_title"] == "文档A"

    @patch("server.services.retriever.Embedder")
    def test_retrieve_empty_result(self, MockEmbedder):
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [[0.1, 0.2]]
        MockEmbedder.return_value = mock_embedder

        mock_store = MagicMock()
        mock_store.search.return_value = []

        retriever = Retriever(vector_store=mock_store, config={})
        results = retriever.retrieve("无相关内容")
        assert results == []

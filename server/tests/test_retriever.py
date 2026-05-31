import pytest
from unittest.mock import MagicMock, patch
from server.services.retriever import Retriever


class TestRetriever:
    @patch("server.services.retriever.SearchService")
    def test_retrieve_returns_chunks_with_scores(self, MockSearchService):
        mock_svc = MagicMock()
        mock_svc.hybrid_search.return_value = [
            {"chunk_id": "c1", "content": "相关段落A", "document_id": "d1", "document_title": "文档A", "file_name": "a.pdf", "score": 0.9, "chunk_no": 1},
            {"chunk_id": "c2", "content": "相关段落B", "document_id": "d1", "document_title": "文档A", "file_name": "a.pdf", "score": 0.7, "chunk_no": 2},
        ]
        MockSearchService.return_value = mock_svc

        retriever = Retriever(vector_store=MagicMock(), config={"retrieval_top_k": "3"})
        results = retriever.retrieve("测试问题")

        assert len(results) == 2
        assert results[0]["score"] >= results[1]["score"]
        assert results[0]["document_title"] == "文档A"

    @patch("server.services.retriever.SearchService")
    def test_retrieve_empty_result(self, MockSearchService):
        mock_svc = MagicMock()
        mock_svc.hybrid_search.return_value = []
        MockSearchService.return_value = mock_svc

        retriever = Retriever(vector_store=MagicMock(), config={})
        results = retriever.retrieve("无相关内容")
        assert results == []

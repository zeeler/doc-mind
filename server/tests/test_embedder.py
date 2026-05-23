import pytest
from unittest.mock import MagicMock, patch
from server.services.embedder import Embedder


class TestEmbedder:
    @patch("server.services.embedder.LLMAdapter")
    def test_embed_returns_list_of_floats(self, MockAdapter):
        mock_client = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]
        mock_client.embeddings.create.return_value = mock_embedding
        mock_adapter = MagicMock()
        mock_adapter.client = mock_client
        mock_adapter.embedding_model = "test-model"
        MockAdapter.return_value = mock_adapter

        embedder = Embedder({})
        result = embedder.embed(["测试文本"])
        assert len(result) == 1
        assert result[0] == [0.1, 0.2, 0.3]

    @patch("server.services.embedder.LLMAdapter")
    def test_embed_batch(self, MockAdapter):
        mock_client = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.data = [
            MagicMock(embedding=[0.1, 0.2]),
            MagicMock(embedding=[0.3, 0.4]),
        ]
        mock_client.embeddings.create.return_value = mock_embedding
        mock_adapter = MagicMock()
        mock_adapter.client = mock_client
        mock_adapter.embedding_model = "test-model"
        MockAdapter.return_value = mock_adapter

        embedder = Embedder({})
        result = embedder.embed(["文本A", "文本B"])
        assert len(result) == 2

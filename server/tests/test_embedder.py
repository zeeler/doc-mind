import pytest
from unittest.mock import MagicMock, patch
from server.services.embedder import Embedder


class TestEmbedder:
    @patch("server.services.embedder.LLMAdapter")
    def test_embed_returns_list_of_floats(self, MockAdapter):
        mock_adapter = MagicMock()
        mock_adapter.embed.return_value = [[0.1, 0.2, 0.3]]
        MockAdapter.return_value = mock_adapter

        embedder = Embedder({})
        result = embedder.embed(["测试文本"])
        assert len(result) == 1
        assert result[0] == [0.1, 0.2, 0.3]

    @patch("server.services.embedder.LLMAdapter")
    def test_embed_batch(self, MockAdapter):
        mock_adapter = MagicMock()
        mock_adapter.embed.return_value = [[0.1, 0.2], [0.3, 0.4]]
        MockAdapter.return_value = mock_adapter

        embedder = Embedder({})
        result = embedder.embed(["文本A", "文本B"])
        assert len(result) == 2

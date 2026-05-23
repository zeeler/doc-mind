import pytest
from server.services.llm import LLMAdapter


class TestLLMAdapter:
    def test_build_client_mlx(self):
        cfg = {
            "llm_provider": "mlx",
            "mlx_api_base": "http://localhost:8080/v1",
            "mlx_chat_model": "qwen2.5-7b",
        }
        adapter = LLMAdapter(cfg)
        client = adapter._build_client()
        assert str(client.base_url).rstrip("/") == "http://localhost:8080/v1"
        assert adapter.chat_model == "qwen2.5-7b"

    def test_build_client_openai(self):
        cfg = {
            "llm_provider": "openai",
            "openai_api_base": "https://api.openai.com/v1",
            "openai_api_key": "sk-test",
            "openai_chat_model": "gpt-4o-mini",
        }
        adapter = LLMAdapter(cfg)
        client = adapter._build_client()
        assert "api.openai.com" in str(client.base_url)
        assert adapter.chat_model == "gpt-4o-mini"

    def test_embedding_model_name(self):
        cfg = {
            "llm_provider": "mlx",
            "mlx_embedding_model": "bge-small",
        }
        adapter = LLMAdapter(cfg)
        assert adapter.embedding_model == "bge-small"

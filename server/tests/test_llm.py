import pytest
from server.services.llm import LLMAdapter


class TestLLMAdapter:
    def test_mlx_provider(self):
        cfg = {
            "llm_provider": "mlx",
            "mlx_api_base": "http://localhost:8080/v1",
            "mlx_chat_model": "qwen2.5-7b",
        }
        adapter = LLMAdapter(cfg)
        assert adapter.provider == "mlx"
        assert adapter.chat_model == "qwen2.5-7b"
        assert adapter.api_type == "openai"

    def test_openai_provider(self):
        cfg = {
            "llm_provider": "openai",
            "openai_api_base": "https://api.openai.com/v1",
            "openai_api_key": "sk-test",
            "openai_chat_model": "gpt-4o-mini",
        }
        adapter = LLMAdapter(cfg)
        assert adapter.provider == "openai"
        assert adapter.chat_model == "gpt-4o-mini"

    def test_custom_openai_format(self):
        cfg = {
            "llm_provider": "custom",
            "custom_api_base": "https://api.example.com/v1",
            "custom_api_key": "sk-test",
            "custom_chat_model": "my-model",
            "custom_api_type": "openai",
        }
        adapter = LLMAdapter(cfg)
        assert adapter.provider == "custom"
        assert adapter.api_type == "openai"
        assert adapter.chat_model == "my-model"

    def test_custom_anthropic_format(self):
        cfg = {
            "llm_provider": "custom",
            "custom_api_base": "https://api.deepseek.com/anthropic",
            "custom_api_key": "sk-test",
            "custom_chat_model": "deepseek-v4-pro",
            "custom_api_type": "anthropic",
        }
        adapter = LLMAdapter(cfg)
        assert adapter.provider == "custom"
        assert adapter.api_type == "anthropic"
        assert adapter.chat_model == "deepseek-v4-pro"

    def test_embedding_model_name(self):
        cfg = {
            "llm_provider": "mlx",
            "mlx_embedding_model": "bge-small",
        }
        adapter = LLMAdapter(cfg)
        assert adapter.embedding_model == "bge-small"

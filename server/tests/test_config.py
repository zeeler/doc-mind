# server/tests/test_config.py
import pytest
from server.database import get_engine, reset_engine
from server.models.base import Base
from server.config import AppConfig, DEFAULTS, has_embedding_model, has_reranker_model


@pytest.fixture
def db_setup(tmp_data_dir, monkeypatch):
    monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
    reset_engine()
    Base.metadata.create_all(bind=get_engine())


class TestAppConfig:
    def test_get_config_returns_defaults(self, tmp_data_dir, monkeypatch, db_setup):
        monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
        cfg = AppConfig()
        defaults = cfg.get_all()
        assert defaults["llm_provider"] == "mlx"
        assert defaults["mlx_chat_model"] == ""
        assert defaults["mlx_embedding_model"] == ""
        assert "openai_api_key" in defaults

    def test_set_and_get_config(self, tmp_data_dir, monkeypatch, db_setup):
        monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
        cfg = AppConfig()
        cfg.set("llm_provider", "openai")
        assert cfg.get("llm_provider") == "openai"

    def test_set_persists_across_instances(self, tmp_data_dir, monkeypatch, db_setup):
        monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
        cfg1 = AppConfig()
        cfg1.set("llm_provider", "openai")
        cfg2 = AppConfig()
        assert cfg2.get("llm_provider") == "openai"

    # ---- 回归测试：最近修复的 bug ----

    def test_all_embedding_config_keys_in_defaults(self):
        """Bug: embedding_enabled 等新 key 不在 DEFAULTS 中，导致保存被 400 拒绝。"""
        required_keys = [
            "embedding_enabled", "embedding_model", "embedding_api_base", "embedding_api_key",
            "reranker_enabled", "reranker_model", "reranker_api_base", "reranker_api_key", "reranker_top_k",
            "ocr_max_workers",
        ]
        for key in required_keys:
            assert key in DEFAULTS, f"配置项 '{key}' 缺失，新增配置时需同步添加到 DEFAULTS"

    def test_all_defaults_have_string_values(self):
        """DEFAULTS 中所有值必须是字符串类型（AppConfig 存储约定）。"""
        for key, value in DEFAULTS.items():
            assert isinstance(value, str), (
                f"DEFAULTS['{key}'] 值必须是 str，当前类型: {type(value).__name__}"
            )

    def test_embedding_api_key_optional(self, tmp_data_dir, monkeypatch, db_setup):
        """Bug: api_key 为空时 Embedder 初始化报 Missing credentials。"""
        monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
        from unittest.mock import patch, MagicMock
        # 模拟 OpenAI client，避免实际连接
        with patch("server.services.embedder.OpenAI") as mock_openai:
            from server.services.embedder import Embedder
            config = {
                "embedding_enabled": "true",
                "embedding_model": "test-model",
                "embedding_api_base": "http://localhost:8088/v1",
                "embedding_api_key": "",  # 空 API key
            }
            embedder = Embedder(config)
            assert embedder._standalone_client is not None
            mock_openai.assert_called_once_with(
                base_url="http://localhost:8088/v1", api_key="not-needed"
            )

    def test_has_embedding_model(self):
        """has_embedding_model 在 embedding_enabled=true 时返回 True。"""
        assert has_embedding_model({"embedding_enabled": "true"}) is True
        assert has_embedding_model({"embedding_enabled": "false"}) is False
        assert has_embedding_model({"embedding_model": "bge-large"}) is True
        assert has_embedding_model({}) is False

    def test_has_reranker_model_false_when_incomplete(self):
        """has_reranker_model 只在开关打开且模型名和地址都填写时返回 True。"""
        assert has_reranker_model({
            "reranker_enabled": "true", "reranker_model": "bge", "reranker_api_base": "http://x"
        }) is True
        assert has_reranker_model({
            "reranker_enabled": "true", "reranker_model": "", "reranker_api_base": "http://x"
        }) is False
        assert has_reranker_model({
            "reranker_enabled": "false", "reranker_model": "bge", "reranker_api_base": "http://x"
        }) is False

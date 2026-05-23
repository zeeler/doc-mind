# server/tests/test_config.py
import pytest
from server.database import get_engine, reset_engine
from server.models.base import Base
from server.config import AppConfig


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

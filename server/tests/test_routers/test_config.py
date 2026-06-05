# server/tests/test_routers/test_config.py
import pytest
from fastapi.testclient import TestClient
from server.main import app


@pytest.fixture
def client(tmp_data_dir, monkeypatch):
    monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.routers.documents.DATA_DIR", tmp_data_dir)
    from server.database import reset_engine
    reset_engine()
    from server.models.base import Base
    from server.database import get_engine
    Base.metadata.create_all(bind=get_engine())
    return TestClient(app)


class TestConfigRoutes:
    def test_get_config(self, client):
        response = client.get("/api/v1/config")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert "llm_provider" in data["data"]

    def test_update_config(self, client):
        response = client.put("/api/v1/config", json={"llm_provider": "openai"})
        assert response.status_code == 200
        get_resp = client.get("/api/v1/config")
        assert get_resp.json()["data"]["llm_provider"] == "openai"

    def test_get_models(self, client):
        response = client.get("/api/v1/config/models")
        assert response.status_code == 200
        data = response.json()
        assert "models" in data["data"]

    # ---- 回归测试：最近修复的 bug ----

    def test_update_embedding_config_keys_accepted(self, client):
        """Bug: embedding_enabled 等新 key 不在 DEFAULTS 中，后端返回 400。"""
        response = client.put("/api/v1/config", json={
            "embedding_enabled": "true",
            "embedding_model": "bge-large",
            "embedding_api_base": "http://localhost:8088/v1",
            "embedding_api_key": "",
        })
        assert response.status_code == 200, f"embedding 配置应被接受: {response.json()}"
        data = response.json()
        assert data["data"]["embedding_enabled"] == "true"
        assert data["data"]["embedding_model"] == "bge-large"

    def test_update_reranker_config_keys_accepted(self, client):
        """Bug: reranker_enabled 等新 key 不在 DEFAULTS 中，后端返回 400。"""
        response = client.put("/api/v1/config", json={
            "reranker_enabled": "true",
            "reranker_model": "bge-reranker",
            "reranker_api_base": "http://localhost:8088/v1",
            "reranker_api_key": "",
            "reranker_top_k": "3",
        })
        assert response.status_code == 200, f"reranker 配置应被接受: {response.json()}"
        data = response.json()
        assert data["data"]["reranker_enabled"] == "true"
        assert data["data"]["reranker_model"] == "bge-reranker"

    def test_unknown_config_key_rejected(self, client):
        """不存在的配置项应返回 400 错误。"""
        response = client.put("/api/v1/config", json={"nonexistent_key": "value"})
        assert response.status_code == 400
        assert "不支持的配置项" in response.json()["detail"]

    def test_ocr_max_workers_accepted(self, client):
        """Bug: ocr_max_workers 不在 DEFAULTS 中导致保存失败。"""
        response = client.put("/api/v1/config", json={"ocr_max_workers": "4"})
        assert response.status_code == 200, f"ocr_max_workers 应被接受: {response.json()}"

    def test_embedding_test_endpoint(self, client):
        """Embedding 测试端点存在且可调用。"""
        response = client.get("/api/v1/config/embedding-test")
        # 未配置时返回 400，配置正确时返回 200
        assert response.status_code in (200, 400)

    def test_reranker_test_endpoint(self, client):
        """Reranker 测试端点存在且可调用。"""
        response = client.get("/api/v1/config/reranker-test")
        assert response.status_code in (200, 400)

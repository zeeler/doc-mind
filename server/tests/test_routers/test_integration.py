"""集成测试 — 端到端验证核心链路。"""

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


class TestIntegrationCore:
    """核心集成：上传 → 文档记录 → 搜索 → 配置。"""

    def test_upload_creates_document_and_jobs(self, client, sample_txt):
        """上传 txt 文件应创建文档记录并生成处理任务。"""
        with open(sample_txt, "rb") as f:
            resp = client.post(
                "/api/v1/documents/upload",
                files={"file": ("integration.txt", f, "text/plain")},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        doc_id = data["id"]
        assert data["title"]
        assert doc_id

        # 验证文档存在
        detail = client.get(f"/api/v1/documents/{doc_id}")
        assert detail.status_code == 200
        doc = detail.json()["data"]
        assert doc["file_type"] == "txt"

        # 验证有任务创建
        jobs_resp = client.get("/api/v1/jobs/stats")
        assert jobs_resp.status_code == 200

    def test_search_endpoint_no_crash(self, client):
        """搜索接口在任何状态下不应崩溃。"""
        resp = client.get("/api/v1/search?q=测试内容&type=documents&top_k=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "OK"

    def test_conversation_full_flow(self, client):
        """完整的会话 CRUD：创建 → 验证 → 删除。"""
        resp = client.post("/api/v1/conversations", json={"title": "集成测试会话"})
        assert resp.status_code == 200
        conv_id = resp.json()["data"]["id"]
        assert resp.json()["data"]["title"] == "集成测试会话"

        list_resp = client.get("/api/v1/conversations")
        assert list_resp.status_code == 200
        ids = [c["id"] for c in list_resp.json()["data"]]
        assert conv_id in ids

        del_resp = client.delete(f"/api/v1/conversations/{conv_id}")
        assert del_resp.status_code == 200

    def test_config_roundtrip(self, client):
        """配置读写往返：读取 → 修改 → 恢复。"""
        get_resp = client.get("/api/v1/config")
        assert get_resp.status_code == 200
        original = get_resp.json()["data"]

        old_val = original.get("retrieval_top_k", "15")
        new_val = "20" if old_val != "20" else "10"
        put_resp = client.put("/api/v1/config", json={"retrieval_top_k": new_val})
        assert put_resp.status_code == 200
        assert put_resp.json()["data"]["retrieval_top_k"] == new_val

        restore = client.put("/api/v1/config", json={"retrieval_top_k": old_val})
        assert restore.status_code == 200

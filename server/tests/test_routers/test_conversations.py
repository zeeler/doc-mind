# server/tests/test_routers/test_conversations.py
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


class TestConversationRoutes:
    def test_create_conversation(self, client):
        response = client.post("/api/v1/conversations", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert "id" in data["data"]

    def test_list_conversations(self, client):
        client.post("/api/v1/conversations", json={})
        response = client.get("/api/v1/conversations")
        data = response.json()
        assert len(data["data"]) >= 1

    def test_get_conversation_with_messages(self, client):
        create_resp = client.post("/api/v1/conversations", json={})
        conv_id = create_resp.json()["data"]["id"]
        response = client.get(f"/api/v1/conversations/{conv_id}")
        data = response.json()
        assert data["data"]["id"] == conv_id
        assert "messages" in data["data"]


class TestTaskListIntegration:
    """模拟前端任务列表的完整加载流程。"""

    def test_task_list_shows_all_conversations(self, client):
        titles = ["第三篇文档", "第一篇文档", "第二篇文档"]
        for t in titles:
            client.post("/api/v1/conversations", json={"title": t})

        response = client.get("/api/v1/conversations")
        data = response.json()
        assert data["code"] == "OK"
        assert len(data["data"]) == 3
        titles_in_order = [d["title"] for d in data["data"]]
        assert titles_in_order == ["第二篇文档", "第一篇文档", "第三篇文档"]

    def test_task_list_empty_on_clean_db(self, client):
        response = client.get("/api/v1/conversations")
        data = response.json()
        assert data["code"] == "OK"
        assert data["data"] == []

    def test_task_list_contains_required_fields(self, client):
        client.post("/api/v1/conversations", json={})
        response = client.get("/api/v1/conversations")
        conv = response.json()["data"][0]
        for field in ["id", "title", "status", "created_at", "message_count"]:
            assert field in conv, f"缺少字段: {field}"
        assert isinstance(conv["id"], str) and len(conv["id"]) > 0
        assert isinstance(conv["message_count"], int)

    def test_task_list_persists_after_page_reload(self, client):
        client.post("/api/v1/conversations", json={})
        client.post("/api/v1/conversations", json={})
        assert len(client.get("/api/v1/conversations").json()["data"]) == 2
        assert len(client.get("/api/v1/conversations").json()["data"]) == 2

    def test_rename_conversation(self, client):
        resp = client.post("/api/v1/conversations", json={})
        conv_id = resp.json()["data"]["id"]
        assert resp.json()["data"]["title"] == "新会话"

        client.put(f"/api/v1/conversations/{conv_id}", json={"title": "差旅报销问题"})

        list_resp = client.get("/api/v1/conversations")
        updated = [d for d in list_resp.json()["data"] if d["id"] == conv_id][0]
        assert updated["title"] == "差旅报销问题"

    def test_rename_nonexistent_returns_404(self, client):
        response = client.put("/api/v1/conversations/nonexistent", json={"title": "x"})
        assert response.status_code == 404

    def test_rename_empty_title_ignored(self, client):
        resp = client.post("/api/v1/conversations", json={})
        conv_id = resp.json()["data"]["id"]
        client.put(f"/api/v1/conversations/{conv_id}", json={"title": "   "})
        detail = client.get(f"/api/v1/conversations/{conv_id}")
        assert detail.json()["data"]["title"] == "新会话"


class TestClickTaskFlow:
    """模拟点击任务 → 查看历史聊天记录的完整链路。"""

    def test_conversation_detail_returns_messages_array(self, client):
        """GET /conversations/{id} 返回 messages 数组（即使是空的）。"""
        resp = client.post("/api/v1/conversations", json={})
        conv_id = resp.json()["data"]["id"]
        detail = client.get(f"/api/v1/conversations/{conv_id}")
        data = detail.json()
        assert data["code"] == "OK"
        assert isinstance(data["data"]["messages"], list)

    def test_click_task_shows_correct_title(self, client):
        """点击不同任务返回对应的标题和数据。"""
        r1 = client.post("/api/v1/conversations", json={"title": "差旅报销"})
        r2 = client.post("/api/v1/conversations", json={"title": "销售分析"})

        d1 = client.get(f"/api/v1/conversations/{r1.json()['data']['id']}").json()
        d2 = client.get(f"/api/v1/conversations/{r2.json()['data']['id']}").json()

        assert d1["data"]["title"] == "差旅报销"
        assert d2["data"]["title"] == "销售分析"

    def test_nonexistent_task_returns_404(self, client):
        """点击不存在的任务返回 404。"""
        response = client.get("/api/v1/conversations/nonexistent")
        assert response.status_code == 404

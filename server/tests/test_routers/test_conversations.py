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

    # ---- 回归测试：对话重命名 bug ----

    def test_rename_persists_after_reload(self, client):
        """Bug: 改名后刷新列表，标题又变回去。"""
        resp = client.post("/api/v1/conversations", json={})
        conv_id = resp.json()["data"]["id"]

        # 改名
        client.put(f"/api/v1/conversations/{conv_id}", json={"title": "新标题"})

        # 模拟页面刷新：重新获取列表
        list1 = client.get("/api/v1/conversations")
        updated1 = [d for d in list1.json()["data"] if d["id"] == conv_id][0]
        assert updated1["title"] == "新标题"

        # 再次"刷新"
        list2 = client.get("/api/v1/conversations")
        updated2 = [d for d in list2.json()["data"] if d["id"] == conv_id][0]
        assert updated2["title"] == "新标题"

    def test_rename_idempotent(self, client):
        """Bug: @blur + @keydown.enter 双重触发导致重复调用。验证重复请求不影响结果。"""
        resp = client.post("/api/v1/conversations", json={})
        conv_id = resp.json()["data"]["id"]

        # 模拟双重触发：连续两次 PUT
        client.put(f"/api/v1/conversations/{conv_id}", json={"title": "最终标题"})
        client.put(f"/api/v1/conversations/{conv_id}", json={"title": "最终标题"})

        detail = client.get(f"/api/v1/conversations/{conv_id}")
        assert detail.json()["data"]["title"] == "最终标题"

    def test_rename_conversation_list_sorted(self, client):
        """Bug: 改名后 loadConversations 可能覆盖列表排序导致 UI 错位。"""
        # 创建多个会话并改名
        r1 = client.post("/api/v1/conversations", json={"title": "A会话"})
        r2 = client.post("/api/v1/conversations", json={"title": "B会话"})

        client.put(f"/api/v1/conversations/{r1.json()['data']['id']}", json={"title": "Z会话"})

        # 列表应包含改名后的标题
        list_data = client.get("/api/v1/conversations").json()["data"]
        titles = [d["title"] for d in list_data]
        assert "Z会话" in titles
        assert "B会话" in titles

    def test_rename_with_special_characters(self, client):
        """改名支持 emoji 和特殊字符。"""
        resp = client.post("/api/v1/conversations", json={})
        conv_id = resp.json()["data"]["id"]

        special_title = "测试 🚀 会员运营 & 数据分析"
        client.put(f"/api/v1/conversations/{conv_id}", json={"title": special_title})

        detail = client.get(f"/api/v1/conversations/{conv_id}")
        assert detail.json()["data"]["title"] == special_title


class TestFrontendTemplateRequirements:
    """前端模板必需变量回归检查 — 防止 return 语句遗漏变量导致功能失效。"""

    def test_rename_variables_in_template(self):
        """Bug: editingId / editTitle 从 setup() return 语句中遗漏，导致改名无反应。"""
        template_path = __import__('pathlib').Path(__file__).resolve().parent.parent.parent / "templates" / "index.html"
        html = template_path.read_text()
        import re

        # 提取 setup() 的 return 语句（跨两行，以分号结束）
        match = re.search(r'return \{([^;]+);', html)
        assert match, "未找到 return 语句"
        returned = match.group(1)

        # 改名必需变量
        required = ["editingId", "editTitle", "renameStart", "renameDone", "convMenuId", "toggleConvMenu"]
        for var in required:
            assert var in returned, (
                f"'{var}' 不在 setup() return 语句中！前端改名功能将失效。\n"
                f"请检查 server/templates/index.html 的 return {...} 是否包含此变量。"
            )

    def test_all_v_model_variables_returned(self):
        """模板中用到的关键 ref 变量都应在 return 中。"""
        template_path = __import__('pathlib').Path(__file__).resolve().parent.parent.parent / "templates" / "index.html"
        html = template_path.read_text()
        import re

        # 提取 return 语句中的所有标识符
        match = re.search(r'return \{([^;]+);', html)
        assert match, "未找到 return 语句"
        returned = match.group(1)
        returned_vars = set(re.findall(r'([a-zA-Z_]\w+)', returned))

        # 改名相关的关键变量
        rename_vars = ["editingId", "editTitle", "renameStart", "renameDone", "convMenuId", "toggleConvMenu"]
        for var in rename_vars:
            assert var in returned_vars, f"'{var}' 应在 return 中"


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

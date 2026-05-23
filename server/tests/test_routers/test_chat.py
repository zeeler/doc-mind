# server/tests/test_routers/test_chat.py
import pytest
import json
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from server.main import app


@pytest.fixture
def mock_rag():
    with patch("server.routers.chat.RAGService") as MockRAG:
        mock_rag = MagicMock()
        mock_rag.ask_sync.return_value = {
            "answer": "上海住宿标准不超过600元/晚",
            "citations": [
                {
                    "source_type": "document_chunk",
                    "chunk_id": "c1",
                    "document_title": "差旅制度.pdf",
                    "file_name": "差旅制度.pdf",
                    "chunk_no": 3,
                    "excerpt": "上海住宿标准不超过600元/晚",
                }
            ],
        }
        MockRAG.return_value = mock_rag
        yield mock_rag


@pytest.fixture
def client(tmp_data_dir, monkeypatch, mock_rag):
    monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.routers.documents.DATA_DIR", tmp_data_dir)
    from server.database import reset_engine
    reset_engine()
    from server.models.base import Base
    from server.database import get_engine
    Base.metadata.create_all(bind=get_engine())
    return TestClient(app)


class TestChatRoutes:
    def test_chat_ask_sync(self, client):
        conv_resp = client.post("/api/v1/conversations", json={})
        conv_id = conv_resp.json()["data"]["id"]

        response = client.post("/api/v1/chat/ask", json={
            "conversation_id": conv_id,
            "question": "上海住宿标准是多少？",
        })
        assert response.status_code == 200
        data = response.json()
        assert "answer" in data["data"]
        assert len(data["data"]["citations"]) > 0
        assert "上海" in data["data"]["answer"]

    def test_chat_ask_saves_messages(self, client):
        conv_resp = client.post("/api/v1/conversations", json={})
        conv_id = conv_resp.json()["data"]["id"]

        client.post("/api/v1/chat/ask", json={
            "conversation_id": conv_id,
            "question": "测试问题",
        })

        conv_detail = client.get(f"/api/v1/conversations/{conv_id}")
        messages = conv_detail.json()["data"]["messages"]
        assert len(messages) == 2  # user + assistant

    def test_chat_ask_conversation_not_found(self, client):
        response = client.post("/api/v1/chat/ask", json={
            "conversation_id": "nonexistent",
            "question": "问题",
        })
        assert response.status_code == 404

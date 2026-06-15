# server/tests/test_conversation_model.py
from sqlalchemy import inspect
from server.database import get_engine, reset_engine
from server.models.base import Base
from server.models.conversation import Conversation, Message


class TestConversationModel:
    def test_conversation_table_exists(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        reset_engine()
        Base.metadata.create_all(bind=get_engine())
        insp = inspect(get_engine())
        columns = {c["name"] for c in insp.get_columns("conversations")}
        assert "id" in columns
        assert "title" in columns
        assert "status" in columns
        assert "created_at" in columns

    def test_conversation_default_status_is_active(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        reset_engine()
        Base.metadata.create_all(bind=get_engine())
        from server.database import get_session_ctx
        with get_session_ctx() as s:
            conv = Conversation(title="测试会话")
            s.add(conv)
            s.flush()
            assert conv.status == "active"

    def test_message_table_exists(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        reset_engine()
        Base.metadata.create_all(bind=get_engine())
        insp = inspect(get_engine())
        columns = {c["name"] for c in insp.get_columns("messages")}
        assert "id" in columns
        assert "conversation_id" in columns
        assert "role" in columns
        assert "content" in columns
        assert "citations_json" in columns
        assert "created_at" in columns

    def test_message_foreign_key_to_conversation(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        reset_engine()
        Base.metadata.create_all(bind=get_engine())
        insp = inspect(get_engine())
        fks = insp.get_foreign_keys("messages")
        assert any(fk["referred_table"] == "conversations" for fk in fks)

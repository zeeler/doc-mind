import pytest
from sqlalchemy import text
from server.database import get_engine, get_session, init_db, reset_engine


class TestDatabase:
    def test_engine_creates_sqlite_url(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        reset_engine()
        engine = get_engine()
        db_path = str(tmp_data_dir / "app.db")
        assert db_path in str(engine.url)

    def test_init_db_creates_tables(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        reset_engine()
        engine = get_engine()
        init_db()
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = [row[0] for row in result]
        assert "documents" in tables
        assert "conversations" in tables
        assert "app_config" in tables

    def test_get_session_yields_session(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        reset_engine()
        session = next(get_session())
        assert session is not None
        session.close()

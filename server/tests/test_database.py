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


class TestFtsIndexCleanup:
    """FTS5 清理相关回归测试。"""

    def _init(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        reset_engine()
        init_db()

    @staticmethod
    def _fts_total() -> int:
        with get_engine().connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM chunks_fts")).fetchone()[0]

    def test_delete_by_document_id_without_chunk_rows(self, tmp_data_dir, monkeypatch):
        """回归：chunk 行不存在时也必须能清掉 FTS 条目。

        旧实现用 `chunk_id IN (SELECT id FROM document_chunks WHERE document_id=?)` 反查，
        而 embedding 失败回滚后 chunk 行从未提交，反查不到 → FTS 条目永久残留。
        现在按 document_id 直接匹配，不再依赖 chunk 行。
        """
        self._init(tmp_data_dir, monkeypatch)
        from server.database import (
            fts_insert, fts_delete_by_document_id, fts_count_orphans,
        )

        fts_insert("chk-x", "doc-x", "一段内容", "标题X")
        assert self._fts_total() == 1
        assert fts_count_orphans() == 1, "应被识别为孤儿（document_chunks 无对应行）"

        fts_delete_by_document_id("doc-x")
        assert self._fts_total() == 0, "FTS 条目未被清理"

    def test_delete_orphans_only_removes_dangling_entries(self, tmp_data_dir, monkeypatch):
        """清理孤儿只删挂空条目，正常条目要保留。"""
        self._init(tmp_data_dir, monkeypatch)
        from server.database import fts_insert, fts_count_orphans, fts_delete_orphans
        from server.models.document import Document, DocumentChunk
        from server.database import get_session_ctx

        with get_session_ctx() as s:
            s.add(Document(id="doc-ok", title="正常文档", file_name="a.txt",
                           file_type="txt", file_path="/tmp/a.txt"))
            s.add(DocumentChunk(id="chk-ok", document_id="doc-ok", chunk_no=1,
                                content="正常内容", token_count=4))
            s.commit()
        fts_insert("chk-ok", "doc-ok", "正常内容", "正常文档")
        fts_insert("chk-gone", "doc-gone", "残留内容", "已删文档")   # 无对应 chunk 行

        assert self._fts_total() == 2
        assert fts_count_orphans() == 1

        removed = fts_delete_orphans()
        assert removed == 1
        assert self._fts_total() == 1, "正常条目被误删"
        assert fts_count_orphans() == 0

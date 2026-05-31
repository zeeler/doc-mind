"""搜索 API 测试。"""
import pytest
from fastapi.testclient import TestClient
from server.main import app


@pytest.fixture
def client(tmp_data_dir, monkeypatch):
    monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.config.DATA_DIR", tmp_data_dir)
    monkeypatch.setattr("server.routers.search.DATA_DIR", tmp_data_dir)
    from server.database import reset_engine
    reset_engine()
    from server.models.base import Base
    from server.database import get_engine
    Base.metadata.create_all(bind=get_engine())
    # FTS5 虚拟表需要手动创建
    import sqlite3
    db_path = str(tmp_data_dir / "app.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id, content, document_title, tokenize='unicode61'
        )
    """)
    # 插入测试文档和 chunks 用于搜索
    conn.execute("""
        INSERT INTO documents (id, title, file_name, file_type, file_path, file_size, status, chunk_count, elapsed_ms, folder_path, category, created_at, updated_at)
        VALUES ('doc-search-1', 'Python入门指南', 'python-guide.pdf', 'pdf', '/tmp/test.pdf', 100, 'done', 2, 0, '', '技术', datetime('now'), datetime('now'))
    """)
    conn.execute("""
        INSERT INTO document_chunks (id, document_id, chunk_no, content, token_count)
        VALUES ('c-search-1', 'doc-search-1', 1, 'Python 是一门强大的编程语言，适用于各种场景。', 20)
    """)
    conn.execute("""
        INSERT INTO document_chunks (id, document_id, chunk_no, content, token_count)
        VALUES ('c-search-2', 'doc-search-1', 2, '机器学习是人工智能的重要分支，Python 在其中应用广泛。', 20)
    """)
    conn.execute("INSERT INTO chunks_fts VALUES ('c-search-1', 'Python 是一门强大的编程语言，适用于各种场景。', 'Python入门指南')")
    conn.execute("INSERT INTO chunks_fts VALUES ('c-search-2', '机器学习是人工智能的重要分支，Python 在其中应用广泛。', 'Python入门指南')")
    conn.commit()
    conn.close()
    return TestClient(app)


class TestSearchRoutes:
    def test_search_empty_query(self, client):
        response = client.get("/api/v1/search")
        assert response.status_code == 400

    def test_search_chunks_no_results(self, client):
        response = client.get("/api/v1/search?q=zzzznonexistent12345")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "OK"
        assert isinstance(data["data"], list)

    def test_search_chunks(self, client):
        response = client.get("/api/v1/search?q=Python")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert len(data["data"]) > 0

    def test_search_documents(self, client):
        response = client.get("/api/v1/search?q=Python&type=documents")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        if data["data"]:
            assert "best_score" in data["data"][0]
            assert "match_count" in data["data"][0]

    def test_search_with_document_filter(self, client):
        response = client.get("/api/v1/search?q=Python&document_id=doc-search-1")
        assert response.status_code == 200

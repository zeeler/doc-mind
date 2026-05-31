"""SearchService 单元测试。"""
import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture
def search_service():
    """创建带测试数据的 SearchService。"""
    td = tempfile.mkdtemp()
    data_dir = Path(td)
    (data_dir / "chroma").mkdir()
    db_path = data_dir / "app.db"

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            title TEXT,
            file_name TEXT,
            file_type TEXT,
            status TEXT,
            folder_path TEXT,
            category TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT,
            chunk_no INTEGER,
            content TEXT,
            token_count INTEGER
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id, content, document_title, tokenize='unicode61'
        )
    """)
    conn.execute("INSERT INTO documents VALUES ('doc1', 'Python入门', 'python.pdf', 'pdf', 'done', '', '技术')")
    conn.execute("INSERT INTO document_chunks VALUES ('c1', 'doc1', 1, 'Python 是一种解释型编程语言，广泛用于数据科学和机器学习。', 20)")
    conn.execute("INSERT INTO document_chunks VALUES ('c2', 'doc1', 2, '机器学习是人工智能的一个分支，专注于从数据中学习模式。', 20)")
    conn.execute("INSERT INTO document_chunks VALUES ('c3', 'doc1', 3, 'Python 拥有丰富的科学计算库，如 NumPy、Pandas 和 Scikit-learn。', 20)")
    conn.execute("INSERT INTO chunks_fts VALUES ('c1', 'Python 是一种解释型编程语言，广泛用于数据科学和机器学习。', 'Python入门')")
    conn.execute("INSERT INTO chunks_fts VALUES ('c2', '机器学习是人工智能的一个分支，专注于从数据中学习模式。', 'Python入门')")
    conn.execute("INSERT INTO chunks_fts VALUES ('c3', 'Python 拥有丰富的科学计算库，如 NumPy、Pandas 和 Scikit-learn。', 'Python入门')")
    conn.commit()
    conn.close()

    os.environ["KB_DATA_DIR"] = td
    from server.database import reset_engine, DATA_DIR
    reset_engine()

    from server.services.search import SearchService
    svc = SearchService(data_dir=data_dir, top_k=10)
    return svc


class TestFTSSearch:
    def test_fts_keyword_search(self, search_service):
        results = search_service._fts_search("Python")
        assert len(results) > 0
        assert any("Python" in r["content"] for r in results)

    def test_fts_no_match(self, search_service):
        results = search_service._fts_search("zzzznonexistent")
        assert len(results) == 0


class TestRRFMerge:
    def test_rrf_merge_ranks(self, search_service):
        kw = [
            {"chunk_id": "c1", "content": "a", "document_title": "t1"},
            {"chunk_id": "c2", "content": "b", "document_title": "t1"},
        ]
        vec = [
            {"chunk_id": "c2", "content": "b", "document_title": "t1"},
            {"chunk_id": "c3", "content": "c", "document_title": "t1"},
        ]
        merged = search_service._rrf_merge(kw, vec)
        # c2 在两个结果中都排位靠前，应排第一
        assert merged[0]["chunk_id"] == "c2"

    def test_rrf_merge_dedup(self, search_service):
        kw = [{"chunk_id": "c1", "content": "a", "document_title": "t"}]
        vec = [{"chunk_id": "c1", "content": "a", "document_title": "t"}]
        merged = search_service._rrf_merge(kw, vec)
        assert len(merged) == 1

    def test_rrf_marks_match_type(self, search_service):
        kw = [{"chunk_id": "c1", "content": "a", "document_title": "t"}]
        vec = [{"chunk_id": "c2", "content": "b", "document_title": "t"}]
        merged = search_service._rrf_merge(kw, vec)
        types = {m["chunk_id"]: m["match_type"] for m in merged}
        assert types["c1"] == "keyword"
        assert types["c2"] == "vector"
        # hybrid item has both keyword and vector ranks
        h = [m for m in merged if m["match_type"] == "hybrid"]
        assert len(h) == 0  # no overlap in this test


class TestHighlight:
    def test_highlight_simple(self):
        from server.services.search import highlight
        result = highlight("Python是一种编程语言", "Python")
        assert "<mark>Python</mark>" in result

    def test_highlight_excerpt(self):
        from server.services.search import highlight
        long_text = "前" * 100 + "Python编程" + "后" * 100
        result = highlight(long_text, "Python", max_len=80)
        assert "<mark>Python</mark>" in result
        assert len(result) <= 80 + len("<mark></mark>") + 10

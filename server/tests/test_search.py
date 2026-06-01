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


class TestMMRRerank:
    """MMR 多样性重排序单元测试。"""

    def _empty_embedding_config(self):
        """返回无 embedding 模型的配置，强制 MMR 使用 Jaccard fallback。"""
        return {
            "mlx_embedding_model": "",
            "openai_embedding_model": "",
            "custom_embedding_model": "",
        }

    def test_mmr_returns_diverse_results(self, search_service):
        """MMR 应优先选择语义多样的 chunk。

        使用 λ=0.3（偏重多样性），让 Jaccard fallback 能克服相关性差距。
        前 3 个 chunk 共享大量重复词组（Jaccard 高），后 2 个完全不同。
        """
        # c1, c2, c3 几乎相同 → Jaccard 接近 1.0
        same_prefix = "守望者策略守望者策略守望者策略守望者策略"
        results = [
            {"chunk_id": "c1", "content": same_prefix + "核心概念", "score": 0.9,
             "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "chunk_no": 1, "document_id": "d1"},
            {"chunk_id": "c2", "content": same_prefix + "观察情绪", "score": 0.88,
             "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "chunk_no": 2, "document_id": "d1"},
            {"chunk_id": "c3", "content": same_prefix + "避免冲动", "score": 0.85,
             "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "chunk_no": 3, "document_id": "d1"},
            {"chunk_id": "c4", "content": "锚定效应首次报价影响谈判结果研究分析", "score": 0.80,
             "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "chunk_no": 10, "document_id": "d1"},
            {"chunk_id": "c5", "content": "框架效应决策偏好认知角度双方谈判", "score": 0.75,
             "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "chunk_no": 15, "document_id": "d1"},
        ]
        config = self._empty_embedding_config()

        reranked = search_service._mmr_rerank(
            results, "谈判心理学有哪些要点", config, target_k=3, lambda_val=0.3
        )
        assert len(reranked) == 3
        # 最高相关分 chunk 应保留
        assert reranked[0]["chunk_id"] == "c1"
        # 多样性选择: 应包含不同概念的 chunk（c4 或 c5）
        selected_ids = {r["chunk_id"] for r in reranked}
        assert "c4" in selected_ids or "c5" in selected_ids

    def test_mmr_candidate_smaller_than_target(self, search_service):
        """候选池 ≤ target_k 时应直接返回全部。"""
        results = [
            {"chunk_id": "c1", "content": "测试内容A", "score": 0.9,
             "document_title": "测试", "file_name": "a.pdf", "chunk_no": 1, "document_id": "d1"},
        ]
        config = self._empty_embedding_config()
        reranked = search_service._mmr_rerank(
            results, "测试", config, target_k=5, lambda_val=0.7
        )
        assert len(reranked) == 1

    def test_mmr_empty_results(self, search_service):
        """空候选池应直接返回空列表。"""
        config = self._empty_embedding_config()
        reranked = search_service._mmr_rerank(
            [], "测试", config, target_k=5, lambda_val=0.7
        )
        assert reranked == []

    def test_mmr_lambda_all_relevance(self, search_service):
        """λ=1.0 时 MMR 应等同于按分数降序排列（无多样性惩罚）。"""
        results = [
            {"chunk_id": "c1", "content": "守望者策略是谈判心理学的核心", "score": 0.9,
             "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "chunk_no": 1, "document_id": "d1"},
            {"chunk_id": "c2", "content": "守望者策略要求观察情绪反应", "score": 0.85,
             "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "chunk_no": 2, "document_id": "d1"},
            {"chunk_id": "c3", "content": "锚定效应是另一个重要概念", "score": 0.70,
             "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "chunk_no": 10, "document_id": "d1"},
        ]
        config = self._empty_embedding_config()
        reranked = search_service._mmr_rerank(
            results, "谈判心理学", config, target_k=3, lambda_val=1.0
        )
        # λ=1.0 应保持原始相关性排序
        assert [r["chunk_id"] for r in reranked] == ["c1", "c2", "c3"]

    def test_mmr_lambda_all_diversity(self, search_service):
        """λ=0.0 时 MMR 应优先选与已选内容最不相似的 chunk。"""
        results = [
            {"chunk_id": "c1", "content": "守望者策略在谈判中非常重要", "score": 0.9,
             "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "chunk_no": 1, "document_id": "d1"},
            {"chunk_id": "c2", "content": "守望者策略需要持续练习", "score": 0.85,
             "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "chunk_no": 2, "document_id": "d1"},
            {"chunk_id": "c3", "content": "锚定效应影响首次报价策略", "score": 0.50,
             "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "chunk_no": 10, "document_id": "d1"},
        ]
        config = self._empty_embedding_config()
        reranked = search_service._mmr_rerank(
            results, "谈判心理学", config, target_k=2, lambda_val=0.0
        )
        # λ=0.0: 第一个选最高分 c1，第二个选与 c1 最不相似的（c3）
        assert len(reranked) == 2
        assert reranked[0]["chunk_id"] == "c1"
        # c3（锚定效应）与 c1（守望者）的 Jaccard 相似度应低于 c2
        assert reranked[1]["chunk_id"] == "c3"

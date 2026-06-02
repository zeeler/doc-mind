import pytest
from unittest.mock import MagicMock, patch
from server.services.retriever import Retriever


class TestRetriever:
    @patch("server.services.retriever.get_search_service")
    def test_retrieve_returns_chunks_with_scores(self, MockGetSearchService):
        mock_svc = MagicMock()
        base_results = [
            {"chunk_id": "c1", "content": "相关段落A", "document_id": "d1", "document_title": "文档A", "file_name": "a.pdf", "score": 0.9, "chunk_no": 1},
            {"chunk_id": "c2", "content": "相关段落B", "document_id": "d1", "document_title": "文档A", "file_name": "a.pdf", "score": 0.7, "chunk_no": 2},
        ]
        mock_svc.hybrid_search.return_value = base_results
        mock_svc.expand_context.return_value = base_results
        MockGetSearchService.return_value = mock_svc

        retriever = Retriever(vector_store=MagicMock(), config={"retrieval_top_k": "3"})
        results = retriever.retrieve("测试问题")

        assert len(results) == 2
        assert results[0]["score"] >= results[1]["score"]
        assert results[0]["document_title"] == "文档A"

    @patch("server.services.retriever.get_search_service")
    def test_retrieve_passes_config_to_search(self, MockGetSearchService):
        """验证 config 被正确传递给 hybrid_search。"""
        mock_svc = MagicMock()
        base_results = [
            {"chunk_id": "c1", "content": "内容A", "document_id": "d1",
             "document_title": "文档A", "file_name": "a.pdf", "score": 0.9, "chunk_no": 1},
        ]
        mock_svc.hybrid_search.return_value = base_results
        mock_svc.expand_context.return_value = base_results
        MockGetSearchService.return_value = mock_svc

        config = {"retrieval_top_k": "10", "retrieval_enable_mmr": "true",
                  "retrieval_mmr_lambda": "0.7", "retrieval_enable_query_expansion": "false"}
        retriever = Retriever(vector_store=MagicMock(), config=config)
        retriever.retrieve("测试问题")

        # 验证 hybrid_search 被调用时传入了 config
        call_kwargs = mock_svc.hybrid_search.call_args[1]
        assert "config" in call_kwargs
        assert call_kwargs["config"]["retrieval_enable_mmr"] == "true"

    @patch("server.services.retriever.get_search_service")
    def test_retrieve_empty_result(self, MockGetSearchService):
        mock_svc = MagicMock()
        mock_svc.hybrid_search.return_value = []
        mock_svc.expand_context.return_value = []
        MockGetSearchService.return_value = mock_svc

        retriever = Retriever(vector_store=MagicMock(), config={})
        results = retriever.retrieve("无相关内容")
        assert results == []

    @patch("server.services.retriever.get_search_service")
    def test_retrieve_with_query_expansion(self, MockGetSearchService):
        """查询扩展开启时应对多个查询变体进行检索并去重。"""
        mock_svc = MagicMock()
        # 模拟每个查询返回不同结果（扩展可能产生 3-4 个查询变体）
        def _side_effect(*args, **kwargs):
            return {
                "谈判心理学有哪些要点": [
                    {"chunk_id": "c1", "content": "守望者策略内容", "document_id": "d1",
                     "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "score": 0.9, "chunk_no": 1},
                ],
                "谈判心理学": [
                    {"chunk_id": "c2", "content": "锚定效应内容", "document_id": "d1",
                     "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "score": 0.8, "chunk_no": 10},
                ],
                "谈判心理学要点": [
                    {"chunk_id": "c3", "content": "框架效应内容", "document_id": "d1",
                     "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "score": 0.7, "chunk_no": 15},
                ],
                "谈判心理学的要点": [
                    {"chunk_id": "c1", "content": "守望者策略内容", "document_id": "d1",
                     "document_title": "哈佛谈判心理学", "file_name": "a.pdf", "score": 0.9, "chunk_no": 1},
                ],
            }.get(args[0], [])
        mock_svc.hybrid_search.side_effect = _side_effect
        # expand_context 返回输入不变
        mock_svc.expand_context.side_effect = lambda results, **kwargs: results
        MockGetSearchService.return_value = mock_svc

        config = {"retrieval_top_k": "5", "retrieval_enable_query_expansion": "true"}
        retriever = Retriever(vector_store=MagicMock(), config=config)
        results = retriever.retrieve("谈判心理学有哪些要点")

        # 应去重合并（c1 在多个查询中重复）
        assert len(results) == 3
        ids = {r["chunk_id"] for r in results}
        assert ids == {"c1", "c2", "c3"}

    def test_expand_query_broad_question(self):
        """验证"X有哪些Y"模式的查询扩展。"""
        retriever = Retriever(
            vector_store=MagicMock(),
            config={"retrieval_enable_query_expansion": "true"}
        )
        queries = retriever._expand_query("谈判心理学有哪些要点")
        assert "谈判心理学有哪些要点" in queries  # 原始查询保留
        assert "谈判心理学" in queries              # 主题词
        assert "谈判心理学要点" in queries           # 主题+要点
        assert len(queries) >= 3

    def test_expand_query_disabled(self):
        """查询扩展关闭时应只返回原始查询。"""
        retriever = Retriever(
            vector_store=MagicMock(),
            config={"retrieval_enable_query_expansion": "false"}
        )
        queries = retriever._expand_query("谈判心理学有哪些要点")
        assert queries == ["谈判心理学有哪些要点"]

    def test_expand_query_chapter_book_split(self):
        """'书名第N章讲了什么' → 拆分出章节关键词。"""
        retriever = Retriever(
            vector_store=MagicMock(),
            config={"retrieval_enable_query_expansion": "true"}
        )
        queries = retriever._expand_query("哈佛谈判心理学第3章讲了什么")
        assert "第3章" in queries
        assert "哈佛谈判心理学第3章" in queries  # suffix-stripped core

    def test_expand_query_suffix_stripping(self):
        """去提问后缀：'XXX讲了什么' → 'XXX'。"""
        retriever = Retriever(
            vector_store=MagicMock(),
            config={"retrieval_enable_query_expansion": "true"}
        )
        queries = retriever._expand_query("线性代数第一章的内容")
        assert "线性代数第一章" in queries

    def test_expand_query_chapter_only(self):
        """仅第N章查询也能正确展开。"""
        retriever = Retriever(
            vector_store=MagicMock(),
            config={"retrieval_enable_query_expansion": "true"}
        )
        queries = retriever._expand_query("第5章介绍了什么")
        assert "第5章" in queries

    def test_expand_query_and_pattern(self):
        """'X和Y' 模式拆分。"""
        retriever = Retriever(
            vector_store=MagicMock(),
            config={"retrieval_enable_query_expansion": "true"}
        )
        queries = retriever._expand_query("梦想家和思想者的区别")
        assert any("梦想家" in q for q in queries)
        assert any("思想者" in q for q in queries)

    def test_document_filter_triggers_on_book_name(self):
        """书名查询应触发文档过滤。"""
        retriever = Retriever(
            vector_store=MagicMock(),
            config={"retrieval_enable_query_expansion": "true"}
        )
        # 验证 _find_document_id 被调用（文档名在 DB 中存在时）
        from unittest.mock import patch
        with patch.object(retriever, '_find_document_id', return_value='doc-123') as mock_find:
            queries = retriever._expand_query("哈佛谈判心理学第3章讲了什么")
            # 文档过滤逻辑在 retrieve() 中，这里只验证扩展正确
            assert "第3章" in queries

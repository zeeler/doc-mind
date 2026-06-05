import pytest
from unittest.mock import MagicMock, patch
from server.services.rag import (
    RAGService, build_qa_prompt, format_citations,
    _build_web_prompt, _build_kb_prompt,
)


class TestRAGService:
    def test_build_qa_prompt(self):
        chunks = [
            {"content": "上海住宿标准不超过600元/晚", "document_title": "差旅制度.pdf", "chunk_id": "c1", "chunk_no": 3},
            {"content": "北京住宿标准不超过500元/晚", "document_title": "差旅制度.pdf", "chunk_id": "c2", "chunk_no": 4},
        ]
        prompt = build_qa_prompt("上海住宿标准是多少？", chunks)
        assert "上海住宿" in prompt
        assert "[1]" in prompt
        assert "[2]" in prompt

    def test_format_citations(self):
        chunks = [
            {"content": "上海住宿标准不超过600元/晚", "document_title": "差旅制度.pdf", "chunk_id": "c1", "file_name": "差旅制度.pdf", "chunk_no": 3},
        ]
        citations = format_citations(chunks)
        assert len(citations) == 1
        assert citations[0]["source_type"] == "document_chunk"
        assert citations[0]["document_title"] == "差旅制度.pdf"

    def test_build_qa_prompt_empty_chunks(self):
        prompt = build_qa_prompt("问题", [])
        assert "问题" in prompt
        assert "知识库中未找到" in prompt

    # ---- 回归测试：最近修复的 bug ----

    def test_build_web_prompt_dict_slicing(self):
        """Bug: dict.fromkeys(...)[:3] 在 Python 3.14 抛 KeyError: slice(None, 3, None)。"""
        chunks = [
            {"content": "内容 A", "document_title": "文档1", "url": "http://a"},
            {"content": "内容 B", "document_title": "文档1", "url": "http://b"},  # 重复标题
            {"content": "内容 C", "document_title": "文档2", "url": "http://c"},
            {"content": "内容 D", "document_title": "文档3", "url": "http://d"},
            {"content": "内容 E", "document_title": "文档4", "url": "http://e"},
        ]
        # 不应抛出异常（特别是 KeyError: slice 错误）
        prompt = _build_web_prompt("测试问题", chunks, "")
        assert "测试问题" in prompt
        assert "文档1" in prompt
        # 标题去重后只取前 3 个（文档1、文档2、文档3）
        assert "文档1" in prompt and "文档2" in prompt and "文档3" in prompt

    def test_build_kb_prompt_dict_slicing(self):
        """Bug: _build_kb_prompt 中 doc_titles[:3] 也应该测试。"""
        chunks = [
            {"content": "内容 A", "document_title": "文档1", "chunk_id": "c1", "chunk_no": 1},
            {"content": "内容 B", "document_title": "文档2", "chunk_id": "c2", "chunk_no": 2},
            {"content": "内容 C", "document_title": "文档3", "chunk_id": "c3", "chunk_no": 3},
            {"content": "内容 D", "document_title": "文档4", "chunk_id": "c4", "chunk_no": 4},
        ]
        prompt = _build_kb_prompt("测试问题", chunks, "")
        assert "测试问题" in prompt
        assert "文档1" in prompt

    def test_is_web_search_needed_rrf_scores(self):
        """Bug: RRF 分数（~0.008-0.016）永远低于旧阈值 0.15，导致每次都触发网络搜索。"""
        from server.services.rag import RAGService

        config = {
            "web_search_enabled": "true",
            "tavily_api_key": "tvly-test123",
            "web_search_max_results": "5",
        }
        mock_retriever = MagicMock()

        with patch("server.services.rag.WebSearchClient") as mock_ws:
            mock_ws.return_value = MagicMock()
            rag = RAGService(mock_retriever, config)

            # 模拟 RRF 分数范围（0.008-0.016）的好结果
            rrf_chunks = [
                {"score": 0.016, "content": "高度相关"},
                {"score": 0.014, "content": "比较相关"},
                {"score": 0.012, "content": "相关"},
                {"score": 0.010, "content": "一般相关"},
            ]
            # 这些分数 > 0.006 且存在 > 0.01 的"好结果"，不应触发网络搜索
            assert rag._is_web_search_needed(rrf_chunks) is False

            # 低质量结果（所有分数 < 0.006）应触发
            low_quality = [
                {"score": 0.004, "content": "低相关"},
                {"score": 0.003, "content": "很低"},
            ]
            assert rag._is_web_search_needed(low_quality) is True

            # 空结果应触发
            assert rag._is_web_search_needed([]) is True

    def test_web_search_supplements_not_replaces_kb(self):
        """Bug: 网络搜索结果完全替换知识库结果，而不是补充。"""
        from server.services.rag import RAGService

        # 足够多的 KB 结果（≥3），防止触发 web search 阈值
        kb_chunks = [
            {"content": "知识库内容 A", "document_title": "KB文档", "chunk_id": "c1",
             "chunk_no": 1, "score": 0.016, "document_id": "d1", "file_name": "kb.pdf"},
            {"content": "知识库内容 B", "document_title": "KB文档", "chunk_id": "c2",
             "chunk_no": 2, "score": 0.014, "document_id": "d1", "file_name": "kb.pdf"},
            {"content": "知识库内容 C", "document_title": "KB文档", "chunk_id": "c3",
             "chunk_no": 3, "score": 0.012, "document_id": "d1", "file_name": "kb.pdf"},
        ]
        web_chunks = [
            {"content": "网络内容", "document_title": "Web标题", "url": "http://x"},
        ]

        config = {
            "web_search_enabled": "true",
            "tavily_api_key": "tvly-test123",
            "web_search_max_results": "5",
        }
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = kb_chunks

        mock_ws = MagicMock()
        mock_ws.search.return_value = web_chunks

        with patch("server.services.rag.WebSearchClient", return_value=mock_ws):
            with patch("server.services.rag.LLMAdapter") as mock_llm:
                mock_llm.return_value.chat.return_value = {"content": "合并回答"}
                rag = RAGService(mock_retriever, config)
                result = rag.ask_sync("测试问题")

                assert result["answer"] == "合并回答"
                mock_retriever.retrieve.assert_called_once()
                # ≥3 个高分 KB chunk 不应触发 web search
                mock_ws.search.assert_not_called()

    def test_web_search_replaces_empty_kb(self):
        """KB 结果为空时 web search 应完全替代（olds behavior for empty KB）。"""
        from server.services.rag import RAGService

        config = {
            "web_search_enabled": "true",
            "tavily_api_key": "tvly-test123",
            "web_search_max_results": "5",
        }
        web_chunks = [
            {"content": "网络内容", "document_title": "Web标题", "url": "http://x"},
        ]

        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []  # 空 KB

        mock_ws = MagicMock()
        mock_ws.search.return_value = web_chunks

        with patch("server.services.rag.WebSearchClient", return_value=mock_ws):
            with patch("server.services.rag.LLMAdapter") as mock_llm:
                mock_llm.return_value.chat.return_value = {"content": "网络回答"}
                rag = RAGService(mock_retriever, config)
                result = rag.ask_sync("测试问题")

                assert result["answer"] == "网络回答"
                mock_retriever.retrieve.assert_called_once()
                # 空 KB 应该触发 web search
                mock_ws.search.assert_called_once()

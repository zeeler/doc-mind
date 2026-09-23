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
        assert "未找到相关内容" in prompt
        assert "不要编造" in prompt

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
        prompt = _build_web_prompt("测试问题", chunks)
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
        prompt = _build_kb_prompt("测试问题", chunks)
        assert "测试问题" in prompt
        assert "文档1" in prompt

    def test_web_search_supplements_not_replaces_kb(self):
        """Bug: 网络搜索结果完全替换知识库结果，而不是补充。"""
        from server.services.rag import RAGService

        # KB 结果充足时，默认勾选联网仍应保留网络来源
        kb_chunks = [
            {"content": "知识库内容 A", "document_title": "KB文档", "chunk_id": "c1",
             "chunk_no": 1, "score": 0.016, "document_id": "d1", "file_name": "kb.pdf"},
            {"content": "知识库内容 B", "document_title": "KB文档", "chunk_id": "c2",
             "chunk_no": 2, "score": 0.014, "document_id": "d1", "file_name": "kb.pdf"},
            {"content": "知识库内容 C", "document_title": "KB文档", "chunk_id": "c3",
             "chunk_no": 3, "score": 0.012, "document_id": "d1", "file_name": "kb.pdf"},
        ]
        web_chunks = [
            {"content": "网络内容", "document_title": "Web标题", "url": "http://x", "match_type": "web"},
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
                # 默认勾选联网：保留网络来源，也保留全部本地片段
                titles = {c["document_title"] for c in result["citations"]}
                assert "Web标题" in titles
                assert sum(1 for c in result["citations"]
                           if c["source_type"] == "document_chunk") == 3

    def test_web_search_serial_when_speculative_disabled(self):
        """勾选联网但禁用并行时，仍应串行搜索网络并保留结果。"""
        from server.services.rag import RAGService

        kb_chunks = [
            {"content": f"知识库内容 {i}", "document_title": "KB文档", "chunk_id": f"c{i}",
             "chunk_no": i, "score": 0.016, "document_id": "d1", "file_name": "kb.pdf"}
            for i in range(1, 4)
        ]
        config = {
            "web_search_enabled": "true",
            "tavily_api_key": "tvly-test123",
            "web_search_max_results": "5",
            "web_search_speculative": "false",
        }
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = kb_chunks
        mock_ws = MagicMock()
        mock_ws.search.return_value = [{"content": "网络内容", "document_title": "Web标题"}]

        with patch("server.services.rag.WebSearchClient", return_value=mock_ws):
            with patch("server.services.rag.LLMAdapter") as mock_llm:
                mock_llm.return_value.chat.return_value = {"content": "回答"}
                rag = RAGService(mock_retriever, config)
                result = rag.ask_sync("测试问题", web_search=True)

                mock_ws.search.assert_called_once()
                assert any(c["document_title"] == "Web标题" for c in result["citations"])

    def test_ask_stream_emits_retrieval_event_before_tokens(self):
        """流式回答应在生成 token 前先推送检索统计，供前端显示命中数量。"""
        import asyncio
        from server.services.rag import RAGService

        kb_chunks = [
            {"content": "知识库内容 A", "document_title": "KB文档", "chunk_id": "c1",
             "chunk_no": 1, "score": 0.5, "document_id": "d1", "file_name": "kb.pdf"},
        ]
        config = {"web_search_enabled": "false"}
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = kb_chunks

        async def _fake_stream(**_kwargs):
            yield {"type": "token", "content": "你好"}
            yield {"type": "done"}

        with patch("server.services.rag.LLMAdapter") as mock_llm:
            mock_llm.return_value.chat_stream = _fake_stream
            rag = RAGService(mock_retriever, config)

            async def _collect():
                return [c async for c in rag.ask_stream("测试问题")]

            events = asyncio.run(_collect())

        assert events[0]["type"] == "retrieval"
        assert events[0]["data"] == {"kb_count": 1, "web_count": 0}
        assert events[1]["type"] == "token"

    def test_web_search_replaces_empty_kb(self):
        """KB 结果为空时 web search 应完全替代（olds behavior for empty KB）。"""
        from server.services.rag import RAGService

        config = {
            "web_search_enabled": "true",
            "tavily_api_key": "tvly-test123",
            "web_search_max_results": "5",
        }
        web_chunks = [
            {"content": "网络内容", "document_title": "Web标题", "url": "http://x", "match_type": "web"},
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

    def test_manual_web_search_gated_by_master_switch(self):
        """Bug: 手动勾选联网搜索绕过 web_search_enabled 总开关。"""
        from server.services.rag import RAGService

        config = {
            "web_search_enabled": "false",  # 总开关关闭
            "tavily_api_key": "tvly-test123",
        }
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []  # 空 KB（自动路径若不受控也会触发）
        mock_ws = MagicMock()

        with patch("server.services.rag.WebSearchClient", return_value=mock_ws):
            with patch("server.services.rag.LLMAdapter") as mock_llm:
                mock_llm.return_value.chat.return_value = {"content": "本地回答"}
                rag = RAGService(mock_retriever, config)
                result = rag.ask_sync("问题", web_search=True)

                assert result["answer"] == "本地回答"
                mock_ws.search.assert_not_called()

    def test_manual_web_search_works_when_enabled(self):
        """总开关开启时，手动勾选联网搜索正常触发。"""
        from server.services.rag import RAGService

        config = {
            "web_search_enabled": "true",
            "tavily_api_key": "tvly-test123",
            "web_search_max_results": "5",
        }
        # 3 个高分 KB chunk，自动补充不会触发，隔离出手动路径
        kb_chunks = [
            {"content": f"KB 内容 {i}", "document_title": "d", "chunk_id": f"c{i}",
             "chunk_no": i, "score": 0.5, "document_id": "d1", "file_name": "f.pdf"}
            for i in range(3)
        ]
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = kb_chunks
        mock_ws = MagicMock()
        mock_ws.search.return_value = [{"content": "网络内容", "document_title": "w", "url": "http://x"}]

        with patch("server.services.rag.WebSearchClient", return_value=mock_ws):
            with patch("server.services.rag.LLMAdapter") as mock_llm:
                mock_llm.return_value.chat.return_value = {"content": "混合回答"}
                rag = RAGService(mock_retriever, config)
                result = rag.ask_sync("问题", web_search=True)

                assert result["answer"] == "混合回答"
                mock_ws.search.assert_called_once()

    def test_doc_ids_passed_to_retriever(self):
        """限定检索范围：doc_ids 应透传到 retriever.retrieve。"""
        from server.services.rag import RAGService

        config = {"web_search_enabled": "false"}
        kb_chunks = [
            {"content": f"KB 内容 {i}", "document_title": "d", "chunk_id": f"c{i}",
             "chunk_no": i, "score": 0.5, "document_id": "d1", "file_name": "f.pdf"}
            for i in range(3)
        ]
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = kb_chunks

        with patch("server.services.rag.LLMAdapter") as mock_llm:
            mock_llm.return_value.chat.return_value = {"content": "回答"}
            rag = RAGService(mock_retriever, config)
            rag.ask_sync("问题", doc_ids=["d1", "d2"])
            mock_retriever.retrieve.assert_called_once_with("问题", doc_ids=["d1", "d2"])


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("parallel", ["false", "true"])
@pytest.mark.parametrize("kb_count", [0, 1, 3])
@pytest.mark.asyncio
async def test_unchecked_web_search_never_calls_search_engine(stream, parallel, kb_count):
    """取消勾选是硬开关：空库、低质量或充足结果、串行/并行都不得联网。"""
    retriever = MagicMock()
    retriever.retrieve.return_value = [
        {"content": "本地内容", "document_title": "本地文档", "chunk_id": f"c{i}", "score": 0.001}
        for i in range(kb_count)
    ]
    config = {"web_search_enabled": "true", "web_search_speculative": parallel,
              "tavily_api_key": "dummy", "anysearch_enabled": "true", "anysearch_api_key": "dummy"}
    with patch("server.services.rag.LLMAdapter") as llm, patch("server.services.rag.WebSearchClient.search", return_value=[]) as search, patch("server.services.rag.AnySearchClient.search", return_value=[]) as anysearch:
        llm.return_value.chat.return_value = {"content": "本地回答"}
        async def tokens(**kwargs):
            yield {"type": "token", "content": "本地回答"}
        llm.return_value.chat_stream = tokens
        rag = RAGService(retriever, config)
        if stream:
            output = [event async for event in rag.ask_stream("问题", web_search=False)]
            assert output[0]["data"]["web_count"] == 0
        else:
            result = rag.ask_sync("问题", web_search=False)
            assert all(c["source_type"] == "document_chunk" for c in result["citations"])
        search.assert_not_called()
        anysearch.assert_not_called()


def test_api_defaults_to_web_search_but_accepts_explicit_false():
    from server.schemas import ChatAskRequest
    assert ChatAskRequest(conversation_id="c", question="问题").web_search is True
    assert ChatAskRequest(conversation_id="c", question="问题", web_search=False).web_search is False


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_default_web_search_keeps_both_sources_with_sufficient_kb(stream):
    retriever = MagicMock()
    retriever.retrieve.return_value = [
        {"content": "本地内容", "document_title": "本地文档", "chunk_id": f"c{i}", "score": 0.5}
        for i in range(3)
    ]
    config = {"web_search_enabled": "true", "web_search_speculative": "false", "tavily_api_key": "dummy"}
    web_hit = {"content": "网络内容", "document_title": "网络文档", "chunk_id": "web-1", "url": "https://example.com", "match_type": "web"}
    with patch("server.services.rag.LLMAdapter") as llm, patch("server.services.rag.WebSearchClient.search", return_value=[web_hit]):
        llm.return_value.chat.return_value = {"content": "回答"}
        async def tokens(**kwargs):
            yield {"type": "token", "content": "回答"}
        llm.return_value.chat_stream = tokens
        rag = RAGService(retriever, config)
        if stream:
            output = [event async for event in rag.ask_stream("问题")]
            citations = next(event["data"] for event in output if event["type"] == "citations")
        else:
            citations = rag.ask_sync("问题")["citations"]
        assert len(citations) == 4
        assert {c["source_type"] for c in citations} == {"document_chunk", "web_search"}

import pytest
from unittest.mock import MagicMock
from server.services.rag import RAGService, build_qa_prompt, format_citations


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

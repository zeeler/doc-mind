from server.services.chunker import chunk_text, estimate_tokens, _split_by_structure


class TestChunker:
    def test_chunk_text_basic(self):
        text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
        chunks = chunk_text(text, chunk_size=20, chunk_overlap=5)
        assert len(chunks) >= 2

    def test_chunk_preserves_content(self):
        text = "这是一段完整的测试文本内容用于验证切块功能。"
        chunks = chunk_text(text, chunk_size=100, chunk_overlap=0)
        combined = "".join(chunks)
        assert "测试文本" in combined

    def test_short_text_single_chunk(self):
        text = "短文本"
        chunks = chunk_text(text, chunk_size=100, chunk_overlap=10)
        assert len(chunks) == 1
        assert chunks[0] == "短文本"

    def test_estimate_tokens(self):
        text = "这是一个测试"
        count = estimate_tokens(text)
        assert count > 0

    def test_structure_chapter_boundary(self):
        """章节标题应作为分块边界。"""
        text = "前言内容。\n\n## 第1章 基础\n\n这是第一章的内容，包含了很多重要的知识点。"
        chunks = chunk_text(text, chunk_size=500, chunk_overlap=50)
        # 章节标题应该出现在某个 chunk 中
        assert any("第1章" in c for c in chunks)

    def test_structure_markdown_header(self):
        """Markdown # 标题应被识别。"""
        text = "前言\n\n## 第1章 概述\n\n第一章内容..."
        chunks = chunk_text(text, chunk_size=500, chunk_overlap=50)
        assert any("第1章" in c for c in chunks)

    def test_split_by_structure_preserves_headings(self):
        """结构分割应保留标题行。"""
        text = "内容A\n\n# 标题1\n\n内容B\n\n## 标题2\n\n内容C"
        parts = _split_by_structure(text)
        headings = [p.strip() for p in parts if p.strip().startswith("#")]
        assert len(headings) >= 2

    def test_split_by_structure_no_markers(self):
        """无结构标记的文本应正常分割。"""
        text = "段落1\n\n段落2\n\n段落3"
        parts = _split_by_structure(text)
        assert len(parts) >= 2

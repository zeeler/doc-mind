from server.services.chunker import chunk_text, estimate_tokens


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

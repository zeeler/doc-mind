import pytest
from pathlib import Path
from server.services.parser import parse_file, SUPPORTED_TYPES


class TestParser:
    def test_parse_txt(self, sample_txt):
        text = parse_file(sample_txt)
        assert "第一段" in text
        assert "第二段" in text

    def test_parse_pdf(self, sample_pdf):
        text = parse_file(sample_pdf)
        assert "测试文档" in text or "人工智能" in text

    def test_unsupported_type_raises(self, tmp_path):
        bad = tmp_path / "test.xyz"
        bad.write_text("hello")
        with pytest.raises(ValueError, match="不支持的文件类型"):
            parse_file(bad)

    def test_supported_types(self):
        assert "pdf" in SUPPORTED_TYPES
        assert "docx" in SUPPORTED_TYPES
        assert "md" in SUPPORTED_TYPES
        assert "txt" in SUPPORTED_TYPES

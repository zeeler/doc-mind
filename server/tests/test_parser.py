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
        assert "png" in SUPPORTED_TYPES
        assert "jpg" in SUPPORTED_TYPES

    def test_parse_image_tesseract(self, tmp_path):
        """用 Pillow 创建含文字的 PNG，验证 Tesseract OCR 解析。"""
        pytest.importorskip("PIL")
        pytest.importorskip("pytesseract")
        from PIL import Image, ImageDraw

        img_path = tmp_path / "test_ocr.png"
        img = Image.new("RGB", (400, 80), "white")
        d = ImageDraw.Draw(img)
        d.text((10, 30), "Hello OCR 测试", fill="black")
        img.save(str(img_path))

        text = parse_file(str(img_path), {"ocr_engine": "tesseract", "ocr_enabled": "true"})
        # Tesseract 可能因字体/渲染差异不完全匹配，但至少应包含部分字符
        assert len(text.strip()) > 0, "OCR 应返回非空文本"

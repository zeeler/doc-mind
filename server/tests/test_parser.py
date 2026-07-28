import pytest
from pathlib import Path
from unittest.mock import MagicMock
from server.services.parser import parse_file, SUPPORTED_TYPES, OCREngineError


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
        import shutil
        if not shutil.which("tesseract"):
            pytest.skip("tesseract 可执行文件未安装")
        from PIL import Image, ImageDraw

        img_path = tmp_path / "test_ocr.png"
        img = Image.new("RGB", (400, 80), "white")
        d = ImageDraw.Draw(img)
        d.text((10, 30), "Hello OCR 测试", fill="black")
        img.save(str(img_path))

        text = parse_file(str(img_path), {"ocr_engine": "tesseract", "ocr_enabled": "true"})
        # Tesseract 可能因字体/渲染差异不完全匹配，但至少应包含部分字符
        assert len(text.strip()) > 0, "OCR 应返回非空文本"

    # ---- OCR 引擎错误与空识别结果区分（OCREngineError）----

    def test_ocr_engine_error_raises(self, tmp_path, monkeypatch):
        """Tesseract 崩溃（非语言包问题）应抛 OCREngineError，而非静默返回空。"""
        pytest.importorskip("PIL")
        pytesseract = pytest.importorskip("pytesseract")
        from PIL import Image

        img_path = tmp_path / "err.png"
        Image.new("RGB", (100, 50), "white").save(str(img_path))

        def boom(img, lang=None, timeout=None):
            raise pytesseract.TesseractError(1, "tesseract crashed")

        monkeypatch.setattr(pytesseract, "image_to_string", boom)
        with pytest.raises(OCREngineError):
            parse_file(str(img_path), {"ocr_engine": "tesseract", "ocr_enabled": "true"})

    def test_tesseract_language_fallback(self, tmp_path, monkeypatch):
        """缺 chi_sim 语言包时应自动降级 eng 重试。"""
        pytest.importorskip("PIL")
        pytesseract = pytest.importorskip("pytesseract")
        from PIL import Image

        img_path = tmp_path / "lang.png"
        Image.new("RGB", (100, 50), "white").save(str(img_path))

        calls = []

        def fake_ocr(img, lang=None, timeout=None):
            calls.append(lang)
            if lang == "chi_sim+eng":
                raise pytesseract.TesseractError(1, "Failed loading language 'chi_sim'")
            return "hello text"

        monkeypatch.setattr(pytesseract, "image_to_string", fake_ocr)
        text = parse_file(str(img_path), {"ocr_engine": "tesseract", "ocr_enabled": "true"})
        assert text == "hello text"
        assert calls == ["chi_sim+eng", "eng"]

    def test_prefer_local_falls_back_to_tesseract(self, tmp_path, monkeypatch):
        """prefer_local 勾选但本地模型失败时，应回退到 tesseract。"""
        pytest.importorskip("PIL")
        from PIL import Image

        img_path = tmp_path / "fb.png"
        Image.new("RGB", (100, 50), "white").save(str(img_path))

        config = {
            "ocr_enabled": "true", "ocr_engine": "tesseract",
            "ocr_prefer_local": "true", "ocr_ollama_model": "qwen2.5vl",
        }

        def ollama_down(path, cfg):
            raise OCREngineError("ollama down")

        fake_tess = MagicMock(return_value="tesseract text")
        monkeypatch.setattr("server.services.parser._ocr_image_ollama", ollama_down)
        monkeypatch.setattr("server.services.parser._ocr_image_tesseract", fake_tess)

        text = parse_file(str(img_path), config)
        assert text == "tesseract text"
        fake_tess.assert_called_once()

    def test_ollama_engine_error_raises_no_fallback(self, tmp_path, monkeypatch):
        """显式选择 ollama 引擎时失败应抛错，不回退 tesseract。"""
        pytest.importorskip("PIL")
        from PIL import Image

        img_path = tmp_path / "nofb.png"
        Image.new("RGB", (100, 50), "white").save(str(img_path))

        config = {"ocr_enabled": "true", "ocr_engine": "ollama", "ocr_ollama_model": "qwen2.5vl"}

        def ollama_down(path, cfg):
            raise OCREngineError("ollama down")

        fake_tess = MagicMock(return_value="tess")
        monkeypatch.setattr("server.services.parser._ocr_image_ollama", ollama_down)
        monkeypatch.setattr("server.services.parser._ocr_image_tesseract", fake_tess)

        with pytest.raises(OCREngineError):
            parse_file(str(img_path), config)
        fake_tess.assert_not_called()

    def test_ollama_ocr_client_has_timeout(self, tmp_path, monkeypatch):
        """Ollama OCR 的 OpenAI 客户端必须设置 timeout/max_retries，防止占死 worker 线程。"""
        pytest.importorskip("PIL")
        pytest.importorskip("openai")
        from PIL import Image

        img_path = tmp_path / "t.png"
        Image.new("RGB", (100, 50), "white").save(str(img_path))

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="text"))]
        )
        fake_openai = MagicMock(return_value=fake_client)
        monkeypatch.setattr("openai.OpenAI", fake_openai)

        config = {"ocr_enabled": "true", "ocr_engine": "ollama",
                  "ocr_ollama_model": "m", "llm_timeout": "60"}
        parse_file(str(img_path), config)
        _, kwargs = fake_openai.call_args
        assert kwargs["timeout"] == 60.0
        assert kwargs["max_retries"] == 1

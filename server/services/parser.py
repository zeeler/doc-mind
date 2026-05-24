"""文档解析 — PDF/Word/Markdown/TXT → 纯文本，支持扫描件 OCR。"""

import io
import logging
from pathlib import Path

logger = logging.getLogger("knowledge-base")

SUPPORTED_TYPES = {"pdf", "docx", "md", "txt", "markdown"}


def parse_file(file_path: str | Path, config: dict | None = None) -> str:
    path = Path(file_path)
    suffix = path.suffix.lower().lstrip(".")

    if suffix not in SUPPORTED_TYPES:
        raise ValueError(f"不支持的文件类型: {suffix}")

    if suffix in ("txt", "md", "markdown"):
        return path.read_text(encoding="utf-8")

    if suffix == "pdf":
        return _parse_pdf(path, config or {})

    if suffix == "docx":
        return _parse_docx(path)

    raise ValueError(f"不支持的文件类型: {suffix}")


def _parse_pdf(path: Path, config: dict) -> str:
    import fitz

    doc = fitz.open(str(path))
    try:
        parts = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                parts.append(text.strip())
        full_text = "\n\n".join(parts)

        # 文本太少（< 100 字符）可能是扫描件，尝试 OCR
        if len(full_text.strip()) < 100 and config.get("ocr_enabled", "true") != "false":
            logger.info(f"PDF 文本量少 ({len(full_text.strip())} 字符)，尝试 OCR")
            ocr_text = _ocr_pdf(doc, config)
            if ocr_text and len(ocr_text) > len(full_text):
                logger.info(f"OCR 成功，提取 {len(ocr_text)} 字符")
                return ocr_text

        return full_text
    finally:
        doc.close()


def _ocr_pdf(doc, config: dict) -> str:
    engine = config.get("ocr_engine", "tesseract")
    if engine == "ollama":
        return _ocr_ollama(doc, config)
    return _ocr_tesseract(doc)


def _ocr_tesseract(doc) -> str:
    """使用 Tesseract OCR 识别扫描件文字。需安装: brew install tesseract tesseract-lang"""
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        logger.warning("pytesseract 未安装，跳过 OCR。pip install pytesseract Pillow")
        return ""

    parts = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _ocr_ollama(doc, config: dict) -> str:
    """使用 Ollama 多模态模型识别扫描件文字。需先: ollama pull llama3.2-vision:11b"""
    try:
        from openai import OpenAI
    except ImportError:
        return ""

    model = config.get("ocr_ollama_model", "llama3.2-vision:11b")
    base_url = config.get("ocr_ollama_base_url", "http://localhost:11434/v1")
    client = OpenAI(base_url=base_url, api_key="ollama")

    parts = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        import base64
        img_b64 = base64.b64encode(img_bytes).decode()

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请提取这张图片中的所有文字，只输出文字内容，不要添加其他说明。"},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    ],
                }],
                max_tokens=4096,
            )
            text = response.choices[0].message.content or ""
            if text.strip():
                parts.append(text.strip())
        except Exception as e:
            logger.warning(f"Ollama OCR 失败: {e}")

    return "\n\n".join(parts)


def _parse_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text.strip())
    return "\n\n".join(parts)

"""文档解析 — PDF/Word/Markdown/TXT → 纯文本，支持扫描件 OCR。"""

import io
import base64
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    total_pages = len(doc)
    try:
        parts = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                parts.append(text.strip())
        full_text = "\n\n".join(parts)
        extracted_len = len(full_text.strip())

        ocr_enabled = config.get("ocr_enabled", "true") != "false"
        if ocr_enabled and extracted_len < 100:
            engine = config.get("ocr_engine", "tesseract")
            engine_label = "本地多模态模型" if engine == "ollama" else "Tesseract"
            logger.info(
                f"PDF 文本量少 ({extracted_len} 字符 / {total_pages} 页)，启动 OCR ({engine_label})"
            )
            ocr_text = _ocr_pdf(doc, config)
            ocr_len = len(ocr_text.strip())
            if ocr_len > extracted_len:
                logger.info(f"OCR 完成: {ocr_len} 字符 (引擎: {engine_label})")
                return ocr_text
            else:
                logger.warning(f"OCR 未产生有效文本 (引擎: {engine_label}, 结果: {ocr_len} 字符)")

        if extracted_len == 0:
            logger.warning(f"PDF 无法提取文本 ({total_pages} 页)，请确认文档不是纯图片扫描件或检查 OCR 配置")
        return full_text
    finally:
        doc.close()


def _ocr_pdf(doc, config: dict) -> str:
    engine = config.get("ocr_engine", "tesseract")
    if engine == "ollama":
        return _ocr_ollama(doc, config)
    return _ocr_tesseract(doc)


def _ocr_tesseract(doc) -> str:
    """使用 Tesseract OCR 识别扫描件文字。"""
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        logger.warning("pytesseract 未安装，跳过 OCR。pip install pytesseract Pillow")
        return ""

    try:
        available = pytesseract.get_languages()
    except Exception:
        available = ["eng"]
    lang = "chi_sim+eng" if "chi_sim" in available else "eng"
    if "chi_sim" not in available:
        logger.warning("Tesseract 缺少中文语言包，仅使用英文 OCR。brew install tesseract-lang")

    parts = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img, lang=lang)
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _ocr_ollama(doc, config: dict) -> str:
    """使用本地多模态模型（Ollama / MLX / 自定义 API）并行识别扫描件。"""
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai 未安装，无法调用 OCR API")
        return ""

    model = config.get("ocr_ollama_model", "")
    base_url = config.get("ocr_ollama_base_url", "http://localhost:11434/v1")
    max_workers = int(config.get("ocr_max_workers", "4"))
    if not model:
        logger.warning("OCR 模型 ID 未配置，请在设置中填写")
        return ""

    pages = list(doc)
    total = len(pages)
    logger.info(f"OCR 并行处理 {total} 页 (模型: {model}, 并发: {max_workers})")

    def page_to_image(page):
        pix = page.get_pixmap(dpi=150)
        return base64.b64encode(pix.tobytes("png")).decode()

    images = [page_to_image(p) for p in pages]

    def ocr_page(idx: int, img_b64: str) -> tuple[int, str]:
        client = OpenAI(base_url=base_url, api_key="ocr")
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
            return idx, response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"OCR 第{idx+1}页失败: {e}")
            return idx, ""

    results = [""] * total
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(ocr_page, i, img): i for i, img in enumerate(images)}
        for f in as_completed(futures):
            idx, text = f.result()
            results[idx] = text.strip()

    non_empty = [r for r in results if r]
    logger.info(f"OCR 并行完成: {len(non_empty)}/{total} 页有内容")
    return "\n\n".join(non_empty)


def _parse_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text.strip())
    return "\n\n".join(parts)

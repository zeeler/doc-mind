"""文档解析 — PDF/Word/Markdown/TXT → 纯文本，支持扫描件 OCR。"""

import base64
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = {"pdf", "docx", "xlsx", "pptx", "mobi", "md", "txt", "markdown"}

# 原生 PDF 引擎（liteparse）的底层 C 库在多线程并发调用时
# 可能出现 malloc double-free 等内存错误。全局锁保证同一时间只有一个线程解析 PDF。
pdf_lock = threading.Lock()


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

    if suffix == "xlsx":
        from server.services.formats.xlsx import parse_xlsx
        return parse_xlsx(path)

    if suffix == "pptx":
        from server.services.formats.pptx import parse_pptx
        return parse_pptx(path)

    if suffix == "mobi":
        from server.services.formats.mobi import parse_mobi
        return parse_mobi(path)

    raise ValueError(f"不支持的文件类型: {suffix}")


def _parse_pdf(path: Path, config: dict) -> str:
    from liteparse import LiteParse

    ocr_enabled = config.get("ocr_enabled", "true") != "false"
    max_workers = int(config.get("ocr_max_workers", "4"))

    # 加锁防止多 Worker 并发调用原生 PDF 引擎导致内存错误
    with pdf_lock:
        parser = LiteParse(ocr_enabled=ocr_enabled, num_workers=max_workers)
        result = parser.parse(str(path))
    text = result.text.strip()
    text_len = len(text)
    page_count = len(result.pages) if result.pages else 0

    if text_len < 100 and ocr_enabled:
        engine = config.get("ocr_engine", "tesseract")
        if engine == "ollama":
            engine_label = "本地多模态模型"
            logger.info(
                f"PDF 文本量少 ({text_len} 字符 / {page_count} 页)，启动 OCR ({engine_label})"
            )
            ocr_text = _ocr_ollama(str(path), page_count, config)
            ocr_len = len(ocr_text.strip())
            if ocr_len > text_len:
                logger.info(f"OCR 完成: {ocr_len} 字符 (引擎: {engine_label})")
                return ocr_text
            else:
                logger.warning(f"OCR 未产生有效文本 (引擎: {engine_label}, 结果: {ocr_len} 字符)")

    if text_len == 0:
        logger.warning(
            f"PDF 无法提取文本 ({page_count} 页)，请确认文档不是纯图片扫描件或检查 OCR 配置"
        )
    return result.text


def _ocr_ollama(path: str, page_count: int, config: dict) -> str:
    """使用本地多模态模型（Ollama / MLX / 自定义 API）并行识别扫描件。

    通过 liteparse 生成页面截图，发送到多模态模型进行 OCR。
    """
    from liteparse import LiteParse

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

    page_numbers = list(range(1, page_count + 1))
    logger.info(f"OCR 并行处理 {page_count} 页 (模型: {model}, 并发: {max_workers})")

    # 使用 liteparse 生成页面截图（加锁防止并发崩溃）
    with pdf_lock:
        img_parser = LiteParse(ocr_enabled=False)
        screenshots = img_parser.screenshot(path, page_numbers=page_numbers)

    def ocr_page(idx: int, s) -> tuple[int, str]:
        client = OpenAI(base_url=base_url, api_key="ocr")
        img_b64 = base64.b64encode(s.image_bytes).decode()
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
            logger.warning(f"OCR 第{idx + 1}页失败: {e}")
            return idx, ""

    results = [""] * page_count
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(ocr_page, i, s): i for i, s in enumerate(screenshots)}
        for f in as_completed(futures):
            idx, text = f.result()
            results[idx] = text.strip()

    non_empty = [r for r in results if r]
    logger.info(f"OCR 并行完成: {len(non_empty)}/{page_count} 页有内容")
    return "\n\n".join(non_empty)


def _parse_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text.strip())
    return "\n\n".join(parts)

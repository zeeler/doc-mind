"""快速扫描器 — 提取文档标题、页数、首段文本，生成 index.md 初版。"""

import logging
from pathlib import Path

logger = logging.getLogger("knowledge-base")


def quick_scan(file_path: str) -> dict:
    """快速扫描文件，返回 {title, format, page_count, preview, size_bytes}。不依赖 LLM。"""
    path = Path(file_path)
    suffix = path.suffix.lower().lstrip(".")
    result = {
        "title": path.stem,
        "format": suffix,
        "page_count": 0,
        "preview": "",
        "size_bytes": path.stat().st_size,
    }
    try:
        if suffix in ("txt", "md", "markdown"):
            text = path.read_text(encoding="utf-8")
            result["page_count"] = max(1, text.count("\n") // 50 + 1)
            result["preview"] = text[:500]
        elif suffix == "pdf":
            from liteparse import LiteParse
            from server.services.parser import pdf_lock
            with pdf_lock:
                parser = LiteParse(ocr_enabled=False)
                parse_result = parser.parse(str(path))
            result["page_count"] = len(parse_result.pages) if parse_result.pages else 0
            result["preview"] = parse_result.text.strip()[:500]
        elif suffix == "docx":
            from docx import Document
            doc = Document(str(path))
            parts = [p.text for p in doc.paragraphs if p.text.strip()]
            result["page_count"] = max(1, len(parts) // 30 + 1)
            result["preview"] = "\n".join(parts[:10])[:500]
    except Exception as e:
        logger.warning(f"快速扫描失败 {path.name}: {e}")

    return result


def build_index_md(info: dict, full_text: str = "") -> str:
    """生成 index.md 内容。"""
    lines = [
        f"# {info.get('title', 'Untitled')}",
        "",
        f"- 格式: {info.get('format', 'unknown')}",
        f"- 页数: {info.get('page_count', 0)}",
        f"- 大小: {info.get('size_bytes', 0)} bytes",
        f"- 状态: {info.get('status', 'unknown')}",
        "",
    ]
    if full_text:
        lines.append(full_text)
    elif info.get("preview"):
        lines.append(f"## 预览\n\n{info['preview']}\n\n> 完整内容正在索引中...")

    return "\n".join(lines)

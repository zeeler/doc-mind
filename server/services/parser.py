"""文档解析 — 将 PDF/Word/Markdown/TXT 转换为纯文本。"""

from pathlib import Path

SUPPORTED_TYPES = {"pdf", "docx", "md", "txt", "markdown"}


def parse_file(file_path: str | Path) -> str:
    path = Path(file_path)
    suffix = path.suffix.lower().lstrip(".")

    if suffix not in SUPPORTED_TYPES:
        raise ValueError(f"不支持的文件类型: {suffix}")

    if suffix in ("txt", "md", "markdown"):
        return path.read_text(encoding="utf-8")

    if suffix == "pdf":
        return _parse_pdf(path)

    if suffix == "docx":
        return _parse_docx(path)

    raise ValueError(f"不支持的文件类型: {suffix}")


def _parse_pdf(path: Path) -> str:
    import fitz

    doc = fitz.open(str(path))
    try:
        parts = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)
    finally:
        doc.close()


def _parse_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text.strip())
    return "\n\n".join(parts)

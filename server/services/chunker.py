"""文本切块 — 按段落 + 长度限制切分文本。"""

import re


def chunk_text(text: str, chunk_size: int = 800, chunk_overlap: int = 100) -> list[str]:
    """将文本按段落切分后，合并为不超过 chunk_size 字符的块。"""
    paragraphs = _split_paragraphs(text)
    chunks = []
    current = ""

    for para in paragraphs:
        if not para.strip():
            continue
        if len(current) + len(para) + 1 <= chunk_size:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) > chunk_size:
                sub_chunks = _split_long_paragraph(para, chunk_size, chunk_overlap)
                chunks.extend(sub_chunks)
                current = ""
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks


def _split_paragraphs(text: str) -> list[str]:
    return re.split(r"\n\s*\n", text)


def _split_long_paragraph(text: str, chunk_size: int, overlap: int) -> list[str]:
    """按句子切分长段落，使用 overlap 保持上下文连接。"""
    sentences = re.split(r"(?<=[。！？.!?])\s*", text)
    chunks = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) <= chunk_size:
            current += sent
        else:
            if current:
                chunks.append(current.strip())
            current = current[-overlap:] + sent if len(current) >= overlap else sent
    if current.strip():
        chunks.append(current.strip())
    return chunks


def estimate_tokens(text: str) -> int:
    """粗略估计 token 数（中文按字，英文按 4 字符 ≈ 1 token）。"""
    chinese_chars = len(re.findall(r"[一-鿿]", text))
    other = text
    for ch in re.findall(r"[一-鿿]", text):
        other = other.replace(ch, "", 1)
    return chinese_chars + len(other) // 4

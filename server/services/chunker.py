"""文本切块 — 按段落 + 长度限制切分文本，尊重文档结构。"""

import re

# 结构边界模式：章节标题、markdown 标题、编号标题
_STRUCTURE_BOUNDARY = re.compile(
    r"^("
    r"#{1,6}\s+"                          # markdown 标题
    r"|第[一二三四五六七八九十百千\d]+[章节部]"  # 第X章/第X节
    r"|Chapter\s+\d+"                      # Chapter 1
    r"|[一二三四五六七八九十]+[\、\.\)）]"       # 一、二、三 开头
    r"|\d+[\、\.\)）]\s"                    # 1. 2) 开头
    r")",
    re.MULTILINE,
)

# 章节级别的强边界 — 这些应该是最大的结构单元
_CHAPTER_BOUNDARY = re.compile(
    r"^(#{1,3}\s+|第[一二三四五六七八九十百千\d]+章|Chapter\s+\d+)",
    re.MULTILINE,
)


def chunk_text(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
    section_chunk_size: int | None = None,
) -> list[str]:
    """将文本按段落切分后，合并为不超过 chunk_size 字符的块。

    会识别文档结构（章节标题），在章节边界处强制分段，
    确保不会把不同章节的内容混在一个 chunk 中。

    参数:
        text: 输入文本
        chunk_size: 普通 chunk 最大字符数
        chunk_overlap: 相邻 chunk 重叠字符数
        section_chunk_size: 结构化分段的 chunk 上限（默认 chunk_size * 2）
    """
    if section_chunk_size is None:
        section_chunk_size = chunk_size * 2

    # 按结构边界分割文本为段落组
    paragraphs = _split_by_structure(text)
    chunks = []
    current = ""
    current_section_title = ""

    for para in paragraphs:
        # 检测是否是章节标题
        is_heading = bool(_STRUCTURE_BOUNDARY.match(para.strip()))

        if is_heading:
            # 标题作为新 section 的开始，先 flush 当前 chunk
            if current:
                chunks.append(current)
                current = ""
            current_section_title = para.strip().lstrip("#").strip()
            current = para
            continue

        if not para.strip():
            continue

        candidate = (current + "\n\n" + para).strip() if current else para
        section_limit = section_chunk_size if current_section_title else chunk_size

        if len(candidate) <= section_limit:
            current = candidate
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


def _split_by_structure(text: str) -> list[str]:
    """按结构边界分割文本，每个段落保持完整。"""
    # 找到所有结构边界的位置
    boundaries = [(m.start(), m.end()) for m in _STRUCTURE_BOUNDARY.finditer(text)]

    if not boundaries:
        # 无结构边界，退化为普通段落分割
        return re.split(r"\n\s*\n", text)

    parts = []
    prev_end = 0

    for start, end in boundaries:
        # 添加边界之前的内容（普通段落）
        if start > prev_end:
            before = text[prev_end:start].strip()
            if before:
                # 对非结构化部分仍按段落分割
                parts.extend(re.split(r"\n\s*\n", before))

        # 添加边界行本身（标题）
        heading = text[start:end].strip()
        if heading:
            parts.append(heading)

        prev_end = end

    # 添加最后一部分
    if prev_end < len(text):
        remaining = text[prev_end:].strip()
        if remaining:
            parts.extend(re.split(r"\n\s*\n", remaining))

    return parts


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

"""MOBI 解析 — 提取电子书正文文本。"""

from pathlib import Path


def parse_mobi(path: Path) -> str:
    from ebooklib import epub
    from bs4 import BeautifulSoup

    # ebooklib 支持 MOBI 通过 epub 接口读取
    book = epub.read_epub(str(path))
    parts = []
    for item in book.get_items_of_type(9):  # ITEM_DOCUMENT = 9
        soup = BeautifulSoup(item.get_content(), "html.parser")
        text = soup.get_text(separator="\n").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)

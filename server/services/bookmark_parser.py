"""Chrome bookmark parser — parses Netscape Bookmark File Format HTML."""

import logging
from bs4 import BeautifulSoup

logger = logging.getLogger("knowledge-base")


def parse_bookmarks_html(file_content: str) -> list[dict]:
    """Parse Netscape Bookmark File Format HTML.

    Uses document-order DT traversal with DL nesting depth to reconstruct
    folder paths. Works around html.parser auto-closing <p> tags.

    Returns list of dicts with keys: title, url, folder_path
    """
    soup = BeautifulSoup(file_content, "html.parser")
    bookmarks = []

    # 用嵌套深度跟踪目录栈：depth 0 = 根
    folder_by_depth: dict[int, str] = {}

    for dt in soup.find_all("dt"):
        # 计算此 DT 被多少层 DL 包裹（深度）
        depth = 0
        parent = dt.parent
        while parent:
            if parent.name == "dl":
                depth += 1
            parent = parent.parent

        h3 = dt.find("h3")
        if h3:
            folder_name = h3.get_text(strip=True) or "未命名文件夹"
            folder_by_depth[depth] = folder_name
            # 清除更深层的目录（退出子目录后不再适用）
            for d in list(folder_by_depth.keys()):
                if d > depth:
                    del folder_by_depth[d]
            continue

        a_tag = dt.find("a")
        if a_tag and a_tag.get("href"):
            # 从深度 1 开始拼接目录路径
            parts = [
                folder_by_depth[d]
                for d in sorted(folder_by_depth.keys())
                if d <= depth
            ]
            folder_path = "/".join(parts) if parts else ""

            bookmarks.append({
                "title": a_tag.get_text(strip=True) or a_tag.get("href", ""),
                "url": a_tag["href"],
                "folder_path": folder_path,
            })

    logger.info(f"bookmark_parser: parsed {len(bookmarks)} bookmarks")
    return bookmarks

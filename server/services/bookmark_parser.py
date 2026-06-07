"""Chrome bookmark parser — parses Netscape Bookmark File Format HTML."""

import logging
from bs4 import BeautifulSoup

logger = logging.getLogger("knowledge-base")


def parse_bookmarks_html(file_content: str) -> list[dict]:
    """Parse Netscape Bookmark File Format HTML.

    Returns list of dicts with keys: title, url, add_date, folder_path
    """
    soup = BeautifulSoup(file_content, "html.parser")
    bookmarks = []

    def walk_dl(dl_element, folder_path: str, depth: int = 0):
        if not dl_element or depth > 20:
            return
        for dt in dl_element.find_all("dt", recursive=False):
            h3 = dt.find("h3")
            if h3:
                folder_name = h3.get_text(strip=True) or "未命名文件夹"
                child_dl = dt.find("dl", recursive=False)
                new_path = f"{folder_path}/{folder_name}" if folder_path else folder_name
                if child_dl:
                    walk_dl(child_dl, new_path, depth + 1)

            a_tag = dt.find("a")
            if a_tag and a_tag.get("href"):
                bookmarks.append({
                    "title": a_tag.get_text(strip=True) or a_tag.get("href", ""),
                    "url": a_tag["href"],
                    "folder_path": folder_path,
                })

    dl = soup.find("dl")
    if dl:
        walk_dl(dl, "")

    logger.info(f"bookmark_parser: parsed {len(bookmarks)} bookmarks")
    return bookmarks

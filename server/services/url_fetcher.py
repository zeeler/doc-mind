"""URL fetcher — downloads and extracts text content from URLs."""

import httpx
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger("knowledge-base")


def fetch_url(url: str, timeout: int = 30) -> dict:
    """Fetch a URL and extract its main text content.

    Returns dict with keys: title, text_content, error
    """
    result = {"title": "", "text_content": "", "error": None}

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; KnowledgeBase/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract title
        if soup.title and soup.title.string:
            result["title"] = soup.title.string.strip()

        # Remove non-content elements
        for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # Extract main content
        main = (
            soup.find("article")
            or soup.select_one('[role="main"]')
            or soup.find(class_="content")
            or soup.find("body")
        )

        if main:
            text = main.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        result["text_content"] = text

    except httpx.HTTPStatusError as e:
        result["error"] = f"HTTP {e.response.status_code}"
    except httpx.TimeoutException:
        result["error"] = "请求超时"
    except Exception as e:
        result["error"] = str(e)
        logger.warning(f"fetch_url failed for {url}: {e}")

    return result

"""URL fetcher — downloads and extracts text content from URLs."""

import ipaddress
import socket
from urllib.parse import urlparse

import httpx
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _is_private_host(host: str | None) -> bool:
    """判断主机是否解析到内网/环回地址（SSRF 防护）。解析失败返回 False（让请求自然报错）。"""
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def fetch_url(url: str, timeout: int = 30) -> dict:
    """Fetch a URL and extract its main text content.

    Returns dict with keys: title, text_content, error
    """
    result = {"title": "", "text_content": "", "error": None}

    # SSRF 防护：拒绝解析到内网/环回地址的 URL（重定向落点也检查）
    if _is_private_host(urlparse(url).hostname):
        result["error"] = "不允许访问内网地址"
        return result

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; KnowledgeBase/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()

        if _is_private_host(resp.url.host):
            result["error"] = "不允许访问内网地址（重定向）"
            return result

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

"""URL fetcher — downloads and extracts text content from URLs."""

import ipaddress
import socket
from urllib.parse import urlparse, urljoin

import httpx
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# SSRF 防护：重定向最大跳数、响应体最大字节数
_MAX_REDIRECTS = 5
_MAX_BODY_BYTES = 5 * 1024 * 1024


def _is_private_host(host: str | None) -> bool:
    """判断主机是否解析到内网/环回地址（SSRF 防护）。解析失败视为危险（fail-closed）。"""
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
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

    # SSRF 防护：拒绝解析到内网/环回地址的 URL（重定向逐跳检查）
    if _is_private_host(urlparse(url).hostname):
        result["error"] = "不允许访问内网地址"
        return result

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; KnowledgeBase/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
        # 关闭自动重定向，手动逐跳跟随：每一跳先检查目标 host 再发请求，
        # 避免「先请求后检查」导致内网请求已经发出
        current_url = url
        html_text = None
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                with client.stream("GET", current_url, headers=headers) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            break
                        next_url = urljoin(current_url, location)
                        if _is_private_host(urlparse(next_url).hostname):
                            result["error"] = "不允许访问内网地址（重定向）"
                            return result
                        current_url = next_url
                        continue
                    resp.raise_for_status()
                    # 限制响应体大小，防止恶意 URL 撑爆内存
                    chunks = []
                    total = 0
                    for chunk in resp.iter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= _MAX_BODY_BYTES:
                            break
                    encoding = resp.encoding or "utf-8"
                    html_text = b"".join(chunks).decode(encoding, errors="replace")
                    break
            else:
                result["error"] = "重定向次数过多"
                return result

        if html_text is None:
            result["error"] = "重定向响应缺少 Location 头"
            return result

        soup = BeautifulSoup(html_text, "html.parser")

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

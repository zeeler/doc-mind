"""Tavily 网络搜索客户端 — 知识库检索不足时的后备方案。"""

import logging
import uuid
import httpx

logger = logging.getLogger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"


class WebSearchClient:
    """Tavily Search API 轻量封装。

    将搜索结果归一化为与 Retriever.retrieve() 相同格式的 chunk 字典，
    使 RAGService 可以统一处理知识库结果和网络搜索结果。
    """

    def __init__(self, api_key: str, max_results: int = 5) -> None:
        self.api_key = api_key.strip()
        self.max_results = max_results

    def search(self, query: str) -> list[dict]:
        """调用 Tavily API，返回 chunk 格式的结果列表。"""
        if not self.api_key:
            logger.warning("Tavily 搜索跳过: API Key 未配置")
            return []

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    TAVILY_API_URL,
                    json={
                        "api_key": self.api_key,
                        "query": query,
                        "max_results": self.max_results,
                        "search_depth": "basic",
                    },
                )
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Tavily HTTP %s: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            return []
        except httpx.RequestError as exc:
            logger.error("Tavily 请求失败: %s", exc)
            return []

        raw_results = body.get("results", [])
        if not raw_results:
            logger.info("Tavily 无结果: query=%r", query)
            return []

        return [
            {
                "chunk_id": f"web-{uuid.uuid4().hex[:12]}",
                "content": r.get("content", ""),
                "score": r.get("score", 0.0),
                "document_title": r.get("title", "Web Search"),
                "file_name": r.get("url", ""),
                "chunk_no": idx + 1,
                "document_id": "web_search",
                "match_type": "web",
                "url": r.get("url", ""),
            }
            for idx, r in enumerate(raw_results)
        ]

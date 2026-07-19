"""AnySearch 网络搜索客户端 — 通过 JSON-RPC API 搜索网页。"""

import logging
import uuid
import httpx

logger = logging.getLogger(__name__)

ANYSEARCH_API_URL = "https://api.anysearch.com/mcp"


class AnySearchClient:
    """AnySearch JSON-RPC API 轻量封装。

    将搜索结果归一化为与 Retriever.retrieve() 相同格式的 chunk 字典，
    使 RAGService 可以统一处理知识库结果和网络搜索结果。
    """

    def __init__(self, api_key: str = "", max_results: int = 5) -> None:
        self.api_key = api_key.strip() if api_key else ""
        self.max_results = max(max_results, 1)

    def search(self, query: str, raise_errors: bool = False) -> list[dict]:
        """调用 AnySearch API，返回 chunk 格式的结果列表。

        raise_errors=True 时不吞掉请求异常（供连接测试端点区分成败）。"""
        if not query.strip():
            return []

        try:
            with httpx.Client(timeout=30.0) as client:
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                resp = client.post(
                    ANYSEARCH_API_URL,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "search",
                            "arguments": {
                                "query": query,
                                "max_results": self.max_results,
                            },
                        },
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "AnySearch HTTP %s: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            if raise_errors:
                raise
            return []
        except httpx.RequestError as exc:
            logger.error("AnySearch 请求失败: %s", exc)
            if raise_errors:
                raise
            return []
        except ValueError as exc:  # resp.json() 解析失败（非 JSON 响应）
            logger.error("AnySearch 响应非 JSON: %s", exc)
            if raise_errors:
                raise
            return []

        # 解析 JSON-RPC 响应: result.content[0].text 是 JSON 字符串
        content_list = body.get("result", {}).get("content", [])
        raw_text = ""
        for item in content_list:
            if item.get("type") == "text":
                raw_text = item.get("text", "")
                break

        if not raw_text:
            logger.info("AnySearch 无结果: query=%r", query)
            return []

        import json
        try:
            search_data = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.warning("AnySearch 响应解析失败: %s", raw_text[:200])
            return []

        results = search_data.get("results", [])
        if not results:
            logger.info("AnySearch 无结果: query=%r", query)
            return []

        return [
            {
                "chunk_id": f"as-{uuid.uuid4().hex[:12]}",
                "content": r.get("snippet", "") or r.get("content", ""),
                "score": r.get("relevance_score", 0.5),
                "document_title": r.get("title", "Web Search"),
                "file_name": r.get("url", ""),
                "chunk_no": idx + 1,
                "document_id": "anysearch",
                "match_type": "web",
                "url": r.get("url", ""),
            }
            for idx, r in enumerate(results)
        ]

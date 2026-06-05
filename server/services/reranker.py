"""Reranker 服务 — 对召回结果进行精排。"""

import logging
import requests
from typing import List

logger = logging.getLogger("knowledge-base")

# Reranker 官方模板格式（BGE-Reranker / vLLM 兼容）
QUERY_TEMPLATE = (
    "<|im_start|>user\n"
    "<Instruct>: 给定一个查询，检索能回答该查询的相关段落。\n"
    "<Query>: {query}<|im_end|>\n"
    "<|im_start|>assistant\n"
)
DOC_TEMPLATE = "<Document>: {doc}<|im_end|>"


class Reranker:
    """Reranker 客户端 — 调用 Reranker API 对文档列表精排。"""

    def __init__(self, config: dict):
        self._enabled = (
            config.get("reranker_enabled") == "true"
            and bool(config.get("reranker_model", "").strip())
        )
        if not self._enabled:
            self._client = None
            return

        base_url = config.get("reranker_api_base", "").strip().rstrip("/")
        api_key = config.get("reranker_api_key", "").strip()
        self._model = config["reranker_model"].strip()
        # 避免双 /v1：base_url 通常已包含 /v1
        if base_url.endswith("/v1"):
            self._url = f"{base_url}/rerank"
        else:
            self._url = f"{base_url}/v1/rerank"

        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

        logger.info("Reranker 就绪: model=%s url=%s", self._model, base_url)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def rerank(self, query: str, documents: List[str], top_k: int = 5) -> list[dict] | None:
        """对文档列表精排，返回排序后的结果。失败返回 None。

        返回格式: [{"index": int, "relevance_score": float}, ...]
        """
        if not self._enabled or not documents:
            return None

        # 按官方模板格式化 query 和 documents
        formatted_query = QUERY_TEMPLATE.format(query=query)
        formatted_docs = [DOC_TEMPLATE.format(doc=doc) for doc in documents]

        try:
            resp = requests.post(
                self._url,
                json={
                    "model": self._model,
                    "query": formatted_query,
                    "documents": formatted_docs,
                    "top_n": top_k,
                },
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            logger.debug(
                "Reranker 精排: %d 文档 → %d 结果", len(documents), len(results)
            )
            return results
        except requests.RequestException as e:
            logger.warning("Reranker API 调用失败: %s", e)
            return None

    def rerank_chunks(
        self, query: str, chunks: list[dict], top_k: int = 5
    ) -> list[dict] | None:
        """对检索到的 chunk 列表精排，返回重排后的 chunk 列表。

        失败时返回 None，调用方应使用原始排序结果。
        """
        if not self._enabled:
            return None
        if len(chunks) <= 1:
            return chunks[:top_k]

        documents = [c["content"] for c in chunks]
        results = self.rerank(query, documents, top_k=top_k)

        # API 调用失败（None 或空列表）
        if not results:
            return None

        reranked = []
        for r in results:
            idx = r["index"]
            if idx < len(chunks):
                chunk = dict(chunks[idx])
                chunk["rerank_score"] = r.get("relevance_score", 0.0)
                chunk["score"] = r.get("relevance_score", 0.0)
                reranked.append(chunk)

        return reranked[:top_k] if reranked else None

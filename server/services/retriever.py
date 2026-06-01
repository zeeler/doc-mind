"""检索服务 — 混合搜索（关键词 + 向量）+ 可选查询扩展。"""

import re
import logging
from server.database import DATA_DIR
from server.services.search import get_search_service

logger = logging.getLogger("knowledge-base")


class Retriever:
    def __init__(self, vector_store, config: dict):
        self.top_k = int(config.get("retrieval_top_k", "15"))
        self.config = config
        self.search_service = get_search_service(data_dir=DATA_DIR, top_k=self.top_k)

    def _expand_query(self, query: str) -> list[str]:
        """查询扩展：针对宽泛问题生成搜索变体，提升覆盖面。

        默认关闭，通过 retrieval_enable_query_expansion 配置控制。
        """
        enabled = self.config.get("retrieval_enable_query_expansion", "false") == "true"
        if not enabled:
            return [query]

        queries = [query]

        # 模式1: "X有哪些Y" → 同时搜索 "X", "X的Y"
        m = re.match(r"(.+?)有哪些(.+)", query)
        if m:
            topic = m.group(1).strip()
            aspect = m.group(2).strip()
            if topic and len(topic) > 1:
                queries.append(topic)
            if topic and aspect:
                queries.append(f"{topic}{aspect}")
                queries.append(f"{topic}的{aspect}")

        # 模式2: "X和Y" → 分别搜索 X, Y
        if "和" in query or "与" in query:
            parts = re.split(r"[和与]", query)
            for p in parts:
                p = p.strip()
                if p and len(p) > 2 and p not in queries:
                    queries.append(p)

        # 模式3: "总结"/"概述"/"介绍" — 提取主题词单独搜索
        m = re.match(r"(总结|概述|介绍|讲讲)(.+?)的?(要点|内容|方面)?$", query)
        if m:
            topic = m.group(2).strip()
            if topic and topic not in queries:
                queries.append(topic)

        if len(queries) > 1:
            logger.info(f"查询扩展: '{query}' → {queries}")

        return queries

    def retrieve(self, query: str) -> list[dict]:
        queries = self._expand_query(query)

        all_results = []
        seen_ids: set[str] = set()

        for q in queries:
            results = self.search_service.hybrid_search(
                q, top_k=self.top_k, config=self.config
            )
            for r in results:
                if r["chunk_id"] not in seen_ids:
                    seen_ids.add(r["chunk_id"])
                    all_results.append(r)

        # 按分数重新排序
        all_results.sort(key=lambda x: x.get("score", 0.0), reverse=True)

        return [
            {
                "chunk_id": r["chunk_id"],
                "content": r["content"],
                "score": r.get("score", 0.0),
                "document_id": r.get("document_id", ""),
                "document_title": r.get("document_title", ""),
                "file_name": r.get("file_name", ""),
                "chunk_no": r.get("chunk_no", 0),
            }
            for r in all_results[:self.top_k]
        ]

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

        # 模式4: "书名第N章..." → 提取章节编号，搜索章节关键词
        m = re.match(r"(.+?)(第[一二三四五六七八九十百千\d]+[章节])", query)
        if m:
            chapter = m.group(2).strip()
            if chapter and chapter not in queries:
                queries.append(chapter)  # 如 "第3章"

        # 去除常见中文提问后缀，保留核心关键词
        for suffix in ["讲了什么", "的内容", "介绍了什么", "是什么", "怎么样", "讲了啥"]:
            if query.endswith(suffix) and len(query) > len(suffix) + 2:
                core = query[: -len(suffix)].strip()
                if core not in queries:
                    queries.append(core)
                break

        if len(queries) > 1:
            logger.info(f"查询扩展: '{query}' → {queries}")

        return queries

    def _find_document_id(self, book_name: str) -> str | None:
        """通过文档名搜索找到匹配的 document_id，用于过滤搜索。"""
        from server.database import get_session_ctx
        from server.models.document import Document
        try:
            with get_session_ctx() as s:
                doc = s.query(Document).filter(
                    Document.title.ilike(f"%{book_name}%")
                ).first()
                return doc.id if doc else None
        except Exception:
            return None

    def retrieve(self, query: str) -> list[dict]:
        queries = self._expand_query(query)

        # 检测查询中是否包含书名 → 尝试定位文档用于过滤
        doc_id_filter = None
        for q in queries:
            if q == query:  # 跳过原始查询
                continue
            doc_id = self._find_document_id(q)
            if doc_id:
                doc_id_filter = doc_id
                break

        all_results = []
        seen_ids: set[str] = set()

        for q in queries:
            # 如果找到了文档 ID，用其过滤章节搜索
            doc_filter = doc_id_filter if doc_id_filter and q != query else None
            results = self.search_service.hybrid_search(
                q, top_k=self.top_k, config=self.config, document_id=doc_filter
            )
            for r in results:
                if r["chunk_id"] not in seen_ids:
                    seen_ids.add(r["chunk_id"])
                    all_results.append(r)

        # 按分数重新排序
        all_results.sort(key=lambda x: x.get("score", 0.0), reverse=True)

        # 上下文扩展：对 top 结果获取相邻 chunk
        context_window = int(self.config.get("retrieval_context_window", "2"))
        if context_window > 0 and all_results:
            top_results = all_results[:max(3, self.top_k // 2)]
            expanded = self.search_service.expand_context(
                top_results, window=context_window
            )
            all_results = expanded

        # 按 chunk_no 排序以保证上下文连贯
        all_results.sort(key=lambda x: (
            x.get("document_id", ""),
            x.get("chunk_no", 0),
        ))

        max_results = int(self.config.get("retrieval_max_results", "50"))
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
            for r in all_results[:max_results]
        ]

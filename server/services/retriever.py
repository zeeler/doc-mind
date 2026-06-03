"""检索服务 — 混合搜索（关键词 + 向量）+ 可选查询扩展。"""

import re
import logging
from server.database import DATA_DIR
from server.services.search import get_search_service

logger = logging.getLogger("knowledge-base")


# 中文数字 → 阿拉伯数字映射
_CN_NUM_MAP = {
    "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
    "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
    "十一": "11", "十二": "12", "十三": "13", "十四": "14", "十五": "15",
    "十六": "16", "十七": "17", "十八": "18", "十九": "19", "二十": "20",
}
_CN_CHAPTER_RE = re.compile(r'第([一二三四五六七八九十]+)([章节])')


def _cn_num_to_arabic(chapter_str: str) -> str:
    """将中文章节编号转为阿拉伯数字，如 '第三章' → '第3章'。"""
    m = _CN_CHAPTER_RE.match(chapter_str)
    if not m:
        return chapter_str
    cn_num = m.group(1)
    suffix = m.group(2)
    arabic = _CN_NUM_MAP.get(cn_num)
    if arabic:
        return f"第{arabic}{suffix}"
    return chapter_str


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

        # 模式4: "书名第N章..." → 提取章节编号，同时尝试中阿两种格式
        m = re.match(r"(.+?)(第[一二三四五六七八九十百千\d]+[章节])", query)
        if m:
            chapter = m.group(2).strip()
            if chapter and chapter not in queries:
                queries.append(chapter)  # 如 "第3章" 或 "第三章"
                # 如果中文数字，也生成阿拉伯数字版本
                arabic = _cn_num_to_arabic(chapter)
                if arabic != chapter and arabic not in queries:
                    queries.append(arabic)  # 如 "第三章" → "第3章"

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

    def _find_document_id(self, book_name: str, session=None) -> str | None:
        """通过文档名搜索找到匹配的 document_id，用于过滤搜索。

        支持书名包含"吴军的《数学之美》"、章节后缀等变体。
        session 为可选的外部 DB 会话，不传则内部创建（兼容外部调用）。
        """
        from server.database import get_session_ctx
        from server.models.document import Document
        import re as _re

        def _candidates(s: str) -> list[str]:
            """生成多个候选书名片段用于匹配。"""
            cand = []
            s = s.strip()
            s = _re.sub(r'[《》「」""]', '', s)
            cand.append(s)
            no_prefix = _re.sub(r'^.+的', '', s).strip()
            if no_prefix and no_prefix != s:
                cand.append(no_prefix)
            no_chapter = _re.sub(r'第[一二三四五六七八九十百千\d]+[章节].*$', '', s).strip()
            if no_chapter and no_chapter not in cand:
                cand.append(no_chapter)
            no_prefix_chapter = _re.sub(r'^.+的', '', no_chapter).strip()
            if no_prefix_chapter and no_prefix_chapter not in cand:
                cand.append(no_prefix_chapter)
            return [c for c in cand if len(c) >= 2]

        def _lookup(s, candidates):
            for c in candidates:
                doc = s.query(Document).filter(
                    Document.title.ilike(f"%{c}%")
                ).first()
                if doc:
                    return doc.id
            return None

        try:
            candidates = _candidates(book_name)
            if session is not None:
                return _lookup(session, candidates)
            with get_session_ctx() as s:
                return _lookup(s, candidates)
        except Exception:
            return None

    def retrieve(self, query: str) -> list[dict]:
        from server.database import get_session_ctx

        queries = self._expand_query(query)

        # 检测查询中是否包含书名 → 尝试定位文档用于过滤
        # 共享一个 DB 会话，避免每个变体都创建新连接
        doc_id_filter = None
        with get_session_ctx() as lookup_session:
            for q in queries:
                if q == query:
                    continue
                doc_id = self._find_document_id(q, session=lookup_session)
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

"""混合搜索服务 — FTS5 关键词 + ChromaDB 向量 + RRF 融合 + 高亮。"""

import re
import sqlalchemy as sa
import logging
from pathlib import Path
from server.vector.store import VectorStore
from server.database import get_engine, space_cjk

logger = logging.getLogger("knowledge-base")

# FTS5 需要转义的特殊字符（会改变查询语义的运算符）
_FTS5_SPECIAL_CHARS_RE = re.compile(r'([\\*()^])')


def _escape_fts5_query(query: str) -> str:
    """转义 FTS5 特殊字符，避免语法错误。

    仅转义 FTS5 运算符: * ( ) ^ \\
    保持查询原样以保留 FTS5 隐式 AND 语义。
    """
    stripped = query.strip()
    escaped = _FTS5_SPECIAL_CHARS_RE.sub(r'\\\g<1>', stripped)
    return escaped


def highlight(text: str, query: str, max_len: int = 160) -> str:
    """在文本中高亮搜索词，截取第一个匹配附近的 excerpt。"""
    tokens = query.strip().split()
    result = text
    for token in tokens:
        result = re.sub(
            f"({re.escape(token)})",
            r"<mark>\1</mark>",
            result,
            flags=re.IGNORECASE,
        )

    first = result.find("<mark>")
    if first >= 0:
        start = max(0, first - max_len // 2)
        end = min(len(result), first + max_len // 2)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(result) else ""
        result = prefix + result[start:end] + suffix

    return result


# 检测纯目录页的 chunk（短文本 + 多个章节标记）
_TOC_PATTERN = re.compile(r'(第[一二三四五六七八九十百千\d]+[章节]|Chapter\s+\d+|第[一二三四五六七八九十百千\d]+部)', re.IGNORECASE)


def _is_toc_chunk(content: str) -> bool:
    """判断 chunk 是否为纯目录页（无实际内容的章节列表）。"""
    stripped = content.strip()
    # 短文本（<300 字符）且包含 ≥3 个章节标记 → 可能是目录
    if len(stripped) < 300:
        markers = _TOC_PATTERN.findall(stripped)
        if len(markers) >= 3:
            return True
    return False


# 常见垃圾内容关键词（出版社信息、公众号推广等）
_JUNK_KEYWORDS = [
    "微信公众号", "小编微信", "微信号", "电子书下载",
    "周读", "ireadweek", "幸福的味道", "行行整理",
    "ISBN", "图书在版编目", "版权所有", "翻印必究",
]


def _is_junk_chunk(content: str) -> bool:
    """判断 chunk 是否为无价值的出版社信息/推广内容。"""
    stripped = content.strip()
    junk_count = sum(1 for kw in _JUNK_KEYWORDS if kw in stripped)
    if junk_count >= 3:
        return True
    if junk_count >= 2 and len(stripped) >= 100:
        return True
    return False


class SearchService:
    def __init__(self, data_dir: Path, top_k: int = 10):
        self.data_dir = data_dir
        self.top_k = top_k
        self._vector_store = None

    @property
    def vector_store(self):
        if self._vector_store is None:
            self._vector_store = VectorStore(persist_dir=str(self.data_dir / "chroma"))
        return self._vector_store


    def _get_query_embedding(self, query: str) -> list[list[float]] | None:
        """使用配置的 embedding 模型编码查询向量；无配置时返回 None。"""
        from server.config import AppConfig, has_embedding_model
        config = AppConfig().get_all()
        if not has_embedding_model(config):
            return None
        try:
            from server.services.embedder import Embedder
            embedder = Embedder(config)
            return embedder.embed([query])
        except Exception as e:
            logger.warning(f"查询 embedding 失败，回退 ChromaDB 内置: {e}")
            return None

    def _fts_search(self, query: str, top_k: int | None = None, document_id: str | None = None) -> list[dict]:
        """FTS5 关键词搜索，返回排名结果。"""
        k = top_k or self.top_k
        cjk_spaced = space_cjk(query)
        escaped_query = _escape_fts5_query(cjk_spaced)
        # 同时搜索 content 和 document_title，标题匹配的 chunk 也会被召回
        # top_k + max_results 限制防止单文档匹配标题时洪水式返回
        query_str = f"(content:({escaped_query}) OR document_title:({escaped_query}))"
        base_sql = """
            SELECT c.id, c.content, d.title, d.file_name, c.chunk_no, d.id as doc_id
            FROM chunks_fts
            JOIN document_chunks c ON chunks_fts.chunk_id = c.id
            JOIN documents d ON c.document_id = d.id
            WHERE chunks_fts MATCH :query
        """
        params: dict = {"query": query_str, "limit": k}
        if document_id:
            base_sql += " AND d.id = :doc_id"
            params["doc_id"] = document_id
        base_sql += " ORDER BY rank LIMIT :limit"

        try:
            with get_engine().connect() as conn:
                rows = conn.execute(sa.text(base_sql), params).fetchall()
            return [
                {
                    "chunk_id": r[0],
                    "content": r[1],
                    "document_title": r[2],
                    "file_name": r[3],
                    "chunk_no": r[4],
                    "document_id": r[5],
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"FTS search error: {e}")
            return []

    def _vector_search(self, query: str, top_k: int | None = None, document_id: str | None = None) -> list[dict]:
        """ChromaDB 向量搜索。"""
        k = top_k or self.top_k
        where = {"document_id": document_id} if document_id else None
        query_embeddings = self._get_query_embedding(query)
        hits = self.vector_store.search(query, top_k=k, where=where, query_embeddings=query_embeddings)
        return [
            {
                "chunk_id": h["id"],
                "content": h["content"],
                "document_title": h.get("metadata", {}).get("title", ""),
                "file_name": h.get("metadata", {}).get("file_name", ""),
                "chunk_no": h.get("metadata", {}).get("chunk_no", 0),
                "document_id": h.get("metadata", {}).get("document_id", ""),
            }
            for h in hits
        ]

    def _rrf_merge(self, keyword_results: list[dict], vector_results: list[dict], k: int = 60, alpha: float = 0.5) -> list[dict]:
        """RRF (Reciprocal Rank Fusion) 结果融合。

        alpha: 关键词搜索权重（0-1），默认 0.5。可通过 retrieval_rrf_alpha 配置。
        """
        info: dict[str, dict] = {}

        for rank, r in enumerate(keyword_results, 1):
            cid = r["chunk_id"]
            info[cid] = {"keyword_rank": rank, "vector_rank": None, "data": r}

        for rank, r in enumerate(vector_results, 1):
            cid = r["chunk_id"]
            if cid in info:
                info[cid]["vector_rank"] = rank
            else:
                info[cid] = {"keyword_rank": None, "vector_rank": rank, "data": r}

        merged = []
        for cid, entry in info.items():
            kw_rank = entry["keyword_rank"]
            vec_rank = entry["vector_rank"]
            score = 0.0
            if kw_rank:
                score += alpha / (k + kw_rank)
            if vec_rank:
                score += (1 - alpha) / (k + vec_rank)

            match_type = "hybrid"
            if kw_rank and not vec_rank:
                match_type = "keyword"
            elif vec_rank and not kw_rank:
                match_type = "vector"

            merged.append({
                **entry["data"],
                "score": round(score, 4),
                "match_type": match_type,
            })

        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged

    def _mmr_rerank(
        self,
        results: list[dict],
        query: str,
        config: dict,
        target_k: int,
        lambda_val: float = 0.7,
    ) -> list[dict]:
        """MMR (Maximal Marginal Relevance) 多样性重排序。

        在候选结果中贪心选择：既与查询相关，又与已选结果语义不同的 chunk。
        有 embedding 模型时使用余弦相似度，否则退化为字符 bigram Jaccard 相似度。
        lambda_val 被钳制在 [0.1, 0.95] 范围内，防止极端值导致退化。
        """
        lambda_val = max(0.1, min(0.95, lambda_val))

        if len(results) <= target_k:
            return results

        # --- 计算相似度矩阵 ---
        from server.config import has_embedding_model
        use_embedding = False
        chunk_embs = None
        query_emb = None

        if has_embedding_model(config):
            try:
                from server.services.embedder import Embedder
                embedder = Embedder(config)
                texts = [query] + [r["content"] for r in results]
                all_embs = embedder.embed(texts)
                query_emb = all_embs[0]
                chunk_embs = all_embs[1:]
                use_embedding = True
            except Exception as e:
                logger.warning(f"MMR embedding 失败，退化为文本相似度: {e}")

        n = len(results)
        scores = [r.get("score", 0.0) for r in results]
        score_min, score_max = min(scores), max(scores)
        if score_max > score_min:
            norm_scores = [(s - score_min) / (score_max - score_min) for s in scores]
        else:
            norm_scores = [0.5] * n

        if use_embedding and chunk_embs is not None and query_emb is not None:
            import numpy as np
            emb_arr = np.array(chunk_embs)
            # 归一化
            norms = np.linalg.norm(emb_arr, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1e-8, norms)
            emb_arr = emb_arr / norms
            sim_matrix = np.dot(emb_arr, emb_arr.T)
            # 查询相关性 = 余弦相似度
            q_arr = np.array(query_emb)
            q_norm = np.linalg.norm(q_arr)
            q_arr = q_arr / (q_norm if q_norm > 0 else 1e-8)
            q_sim = np.dot(emb_arr, q_arr)
            # 综合相关分: 0.5 × 查询余弦 + 0.5 × RRF 归一化分数
            relevance = 0.5 * q_sim + 0.5 * np.array(norm_scores)
        else:
            # 文本相似度 fallback: 字符 bigram Jaccard
            def _jaccard_sim(a: str, b: str) -> float:
                set_a = {a[i:i + 2] for i in range(len(a) - 1)}
                set_b = {b[i:i + 2] for i in range(len(b) - 1)}
                if not set_a or not set_b:
                    return 0.0
                return len(set_a & set_b) / len(set_a | set_b)

            contents = [r["content"] for r in results]
            sim_matrix = [[_jaccard_sim(contents[i], contents[j]) for j in range(n)]
                          for i in range(n)]
            import numpy as np
            relevance = np.array(norm_scores)

        # --- 贪心 MMR 选择 ---
        selected: list[int] = []
        candidates = list(range(n))

        for _ in range(min(target_k, n)):
            best_score = -1.0
            best_idx = -1
            for i in candidates:
                rel = lambda_val * float(relevance[i])
                if selected:
                    div = (1.0 - lambda_val) * max(
                        float(sim_matrix[i][j]) for j in selected
                    )
                else:
                    div = 0.0
                mmr = rel - div
                if mmr > best_score:
                    best_score = mmr
                    best_idx = i
            if best_idx >= 0:
                selected.append(best_idx)
                candidates.remove(best_idx)

        return [results[i] for i in selected]

    def hybrid_search(self, query: str, top_k: int | None = None, document_id: str | None = None, config: dict | None = None) -> list[dict]:
        """混合搜索：FTS5 关键词 + 向量搜索（有外部 embedding 时）+ RRF 融合 + 可选 MMR。

        无外部 embedding 模型时跳过 ChromaDB 向量搜索，仅用 FTS5，
        避免 ChromaDB 内置英文 embedding 对中文的低质量搜索和卡顿。
        """
        from server.config import has_embedding_model

        k = top_k or self.top_k
        if config:
            fetch_mult = int(config.get("retrieval_fetch_multiplier", "2"))
        else:
            fetch_mult = 2
        fetch_k = k * fetch_mult

        keyword_results = self._fts_search(query, top_k=fetch_k, document_id=document_id)

        # 仅在有外部 embedding 模型时使用向量搜索
        use_vector = config and has_embedding_model(config) if config else False
        if use_vector:
            try:
                vector_results = self._vector_search(query, top_k=fetch_k, document_id=document_id)
            except Exception as e:
                logger.warning(f"向量搜索失败，仅用 FTS5: {e}")
                vector_results = []
                use_vector = False
        else:
            vector_results = []

        if use_vector and vector_results:
            rrf_alpha = float(config.get("retrieval_rrf_alpha", "0.5")) if config else 0.5
            merged = self._rrf_merge(keyword_results, vector_results, alpha=rrf_alpha)
        else:
            # 纯 FTS5 模式：添加 match_type 和占位 score
            merged = []
            for r in keyword_results[:k]:
                merged.append({
                    **r,
                    "score": round(1.0 / (1 + keyword_results.index(r)), 4),
                    "match_type": "keyword",
                })

        # MMR 多样性重排序
        if config and config.get("retrieval_enable_mmr", "true") == "true" and len(merged) > k:
            lambda_val = float(config.get("retrieval_mmr_lambda", "0.7"))
            candidate_pool = merged[:min(len(merged), k * 3)]
            merged = self._mmr_rerank(candidate_pool, query, config, k, lambda_val)

        # 过滤纯目录 chunk 和垃圾内容（公众号、ISBN、推荐语列表等）
        merged = [r for r in merged if not _is_toc_chunk(r.get("content", ""))]
        merged = [r for r in merged if not _is_junk_chunk(r.get("content", ""))]

        for r in merged:
            r["excerpt"] = highlight(r["content"], query)

        return merged[:k]

    def document_search(self, query: str, top_k: int | None = None, config: dict | None = None) -> list[dict]:
        """文档级搜索：按文档聚合、去重、取最佳匹配。"""
        chunks = self.hybrid_search(query, top_k=top_k or self.top_k * 3, config=config)

        docs: dict[str, dict] = {}
        for c in chunks:
            did = c["document_id"]
            if did not in docs:
                docs[did] = {
                    "document_id": did,
                    "title": c["document_title"],
                    "best_score": c["score"],
                    "match_count": 1,
                    "top_excerpts": [c["excerpt"]],
                }
            else:
                docs[did]["match_count"] += 1
                docs[did]["best_score"] = max(docs[did]["best_score"], c["score"])
                if len(docs[did]["top_excerpts"]) < 3:
                    docs[did]["top_excerpts"].append(c["excerpt"])

        result = sorted(docs.values(), key=lambda x: x["best_score"], reverse=True)
        return result[: top_k or self.top_k]


    def expand_context(
        self,
        results: list[dict],
        window: int = 2,
    ) -> list[dict]:
        """对检索结果扩展上下文：获取每个匹配 chunk 前后各 window 个相邻 chunk。

        参数:
            results: hybrid_search() 返回的结果列表
            window: 前后各取几个相邻 chunk（默认 2，即前后各 2 个）

        返回:
            包含原始结果和相邻 chunk 的去重列表（按 chunk_no 排序）
        """
        if not results:
            return results

        # 收集需要扩展的 (document_id, chunk_no) 对
        expand_requests: dict[str, set[int]] = {}
        for r in results:
            did = r.get("document_id", "")
            cno = r.get("chunk_no", 0)
            if did and cno:
                if did not in expand_requests:
                    expand_requests[did] = set()
                for offset in range(-window, window + 1):
                    if offset != 0:
                        expand_requests[did].add(cno + offset)

        if not expand_requests:
            return results

        # 排除已有的 chunk_no
        existing = {(r.get("document_id", ""), r.get("chunk_no", 0)) for r in results}

        # 从 SQLite 批量获取相邻 chunks
        import sqlalchemy as sa
        from server.database import get_engine, space_cjk

        expanded = list(results)
        seen_chunk_ids = {r.get("chunk_id", "") for r in results}

        try:
            with get_engine().connect() as conn:
                for did, chunk_nos in expand_requests.items():
                    # 只请求不存在的 chunk_no
                    needed = [n for n in chunk_nos if (did, n) not in existing and n > 0]
                    if not needed:
                        continue

                    # SQLite 不支持 IN :param 绑定元组，需要动态构建占位符
                    placeholders = ",".join([f":cn_{i}" for i in range(len(needed))])
                    params = {"did": did}
                    for i, cn in enumerate(needed):
                        params[f"cn_{i}"] = cn

                    rows = conn.execute(
                        sa.text(f"""
                            SELECT dc.id, dc.content, d.title, d.file_name, dc.chunk_no, d.id as doc_id
                            FROM document_chunks dc
                            JOIN documents d ON dc.document_id = d.id
                            WHERE dc.document_id = :did AND dc.chunk_no IN ({placeholders})
                            ORDER BY dc.chunk_no
                        """),
                        params,
                    ).fetchall()

                    for row in rows:
                        chunk_id = row[0]
                        if chunk_id not in seen_chunk_ids:
                            seen_chunk_ids.add(chunk_id)
                            expanded.append({
                                "chunk_id": chunk_id,
                                "content": row[1],
                                "document_title": row[2],
                                "file_name": row[3],
                                "chunk_no": row[4],
                                "document_id": row[5],
                                "score": 0.0,  # 相邻 chunk 无相关性分数
                                "match_type": "context_expansion",
                            })
        except Exception as e:
            logger.warning(f"上下文扩展失败: {e}")

        # 按 chunk_no 排序以保持上下文连贯性
        expanded.sort(key=lambda x: (x.get("document_id", ""), x.get("chunk_no", 0)))
        return expanded


_search_service_cache: dict[tuple, "SearchService"] = {}


def get_search_service(data_dir: Path, top_k: int = 10) -> SearchService:
    """获取缓存的 SearchService 实例，避免每次请求重建 VectorStore/ChromaDB 客户端。"""
    key = (str(data_dir), top_k)
    if key not in _search_service_cache:
        _search_service_cache[key] = SearchService(data_dir=data_dir, top_k=top_k)
    return _search_service_cache[key]

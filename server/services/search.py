"""混合搜索服务 — FTS5 关键词 + ChromaDB 向量 + RRF 融合 + 高亮。"""

import re
import sqlalchemy as sa
import logging
from pathlib import Path
from server.vector.store import VectorStore
from server.database import get_engine

logger = logging.getLogger("knowledge-base")

# FTS5 特殊字符，按字面值搜索时需要转义或引用
_FTS5_SPECIAL_RE = re.compile(r'[\\"*()^]')


def _escape_fts5_query(query: str) -> str:
    """将用户输入转为 FTS5 字面值搜索，避免特殊字符被解释为运算符。

    策略：将整个查询用双引号包裹作为 phrase query，
    同时内部的双引号进行转义。如果查询中已经有逻辑运算符
    (AND/OR/NOT/NEAR)，则仍然保留原始语义。
    """
    stripped = query.strip()
    # 检测是否包含 FTS5 布尔运算符（大写，作为独立词）
    has_bool_ops = bool(re.search(r'\b(AND|OR|NOT|NEAR)\b', stripped))
    if has_bool_ops:
        # 用户可能意图使用高级搜索，保持原样但转义括号等危险字符
        escaped = _FTS5_SPECIAL_RE.sub(r'\\\g<0>', stripped)
        return escaped
    # 普通搜索：将整个查询作为 phrase，内部双引号转义
    escaped = stripped.replace('"', '""')
    return f'"{escaped}"'


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
        escaped_query = _escape_fts5_query(query)
        base_sql = """
            SELECT c.id, c.content, d.title, d.file_name, c.chunk_no, d.id as doc_id
            FROM chunks_fts
            JOIN document_chunks c ON chunks_fts.chunk_id = c.id
            JOIN documents d ON c.document_id = d.id
            WHERE chunks_fts MATCH :query
        """
        params: dict = {"query": escaped_query, "limit": k}
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
        """RRF (Reciprocal Rank Fusion) 结果融合。"""
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
        """
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
        """混合搜索：FTS5 关键词 + 向量搜索 + RRF 融合 + 可选 MMR 多样性重排序。"""
        k = top_k or self.top_k
        if config:
            fetch_mult = int(config.get("retrieval_fetch_multiplier", "2"))
        else:
            fetch_mult = 2
        fetch_k = k * fetch_mult

        keyword_results = self._fts_search(query, top_k=fetch_k, document_id=document_id)
        vector_results = self._vector_search(query, top_k=fetch_k, document_id=document_id)

        merged = self._rrf_merge(keyword_results, vector_results)

        # MMR 多样性重排序
        if config and config.get("retrieval_enable_mmr", "true") == "true" and len(merged) > k:
            lambda_val = float(config.get("retrieval_mmr_lambda", "0.7"))
            candidate_pool = merged[:min(len(merged), k * 3)]
            merged = self._mmr_rerank(candidate_pool, query, config, k, lambda_val)

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


_search_service_cache: dict[tuple, "SearchService"] = {}


def get_search_service(data_dir: Path, top_k: int = 10) -> SearchService:
    """获取缓存的 SearchService 实例，避免每次请求重建 VectorStore/ChromaDB 客户端。"""
    key = (str(data_dir), top_k)
    if key not in _search_service_cache:
        _search_service_cache[key] = SearchService(data_dir=data_dir, top_k=top_k)
    return _search_service_cache[key]

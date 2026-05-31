"""混合搜索服务 — FTS5 关键词 + ChromaDB 向量 + RRF 融合 + 高亮。"""

import re
import sqlite3
import logging
from pathlib import Path
from server.vector.store import VectorStore

logger = logging.getLogger("knowledge-base")


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
        self.db_path = str(data_dir / "app.db")
        self._vector_store = None

    @property
    def vector_store(self):
        if self._vector_store is None:
            self._vector_store = VectorStore(persist_dir=str(self.data_dir / "chroma"))
        return self._vector_store

    def _fts_search(self, query: str, top_k: int | None = None, document_id: str | None = None) -> list[dict]:
        """FTS5 关键词搜索，返回排名结果。"""
        k = top_k or self.top_k
        conn = sqlite3.connect(self.db_path)
        try:
            base_sql = """
                SELECT c.id, c.content, d.title, d.file_name, c.chunk_no, d.id as doc_id
                FROM chunks_fts
                JOIN document_chunks c ON chunks_fts.chunk_id = c.id
                JOIN documents d ON c.document_id = d.id
                WHERE chunks_fts MATCH ?
            """
            params = [query]
            if document_id:
                base_sql += " AND d.id = ?"
                params.append(document_id)
            base_sql += " ORDER BY rank LIMIT ?"
            params.append(k)

            rows = conn.execute(base_sql, params).fetchall()
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
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS search error: {e}")
            return []
        finally:
            conn.close()

    def _vector_search(self, query: str, top_k: int | None = None, document_id: str | None = None) -> list[dict]:
        """ChromaDB 向量搜索。"""
        k = top_k or self.top_k
        where = {"document_id": document_id} if document_id else None
        hits = self.vector_store.search(query, top_k=k, where=where)
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

    def hybrid_search(self, query: str, top_k: int | None = None, document_id: str | None = None) -> list[dict]:
        """混合搜索：FTS5 关键词 + 向量搜索 + RRF 融合。"""
        k = top_k or self.top_k
        fetch_k = k * 2

        keyword_results = self._fts_search(query, top_k=fetch_k, document_id=document_id)
        vector_results = self._vector_search(query, top_k=fetch_k, document_id=document_id)

        merged = self._rrf_merge(keyword_results, vector_results)

        for r in merged:
            r["excerpt"] = highlight(r["content"], query)

        return merged[:k]

    def document_search(self, query: str, top_k: int | None = None) -> list[dict]:
        """文档级搜索：按文档聚合、去重、取最佳匹配。"""
        chunks = self.hybrid_search(query, top_k=top_k or self.top_k * 3)

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

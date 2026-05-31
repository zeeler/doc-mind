"""检索服务 — 混合搜索（关键词 + 向量）。"""

from server.database import DATA_DIR
from server.services.search import SearchService


class Retriever:
    def __init__(self, vector_store, config: dict):
        self.top_k = int(config.get("retrieval_top_k", "5"))
        self.search_service = SearchService(data_dir=DATA_DIR, top_k=self.top_k)

    def retrieve(self, query: str) -> list[dict]:
        results = self.search_service.hybrid_search(query, top_k=self.top_k)
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
            for r in results
        ]

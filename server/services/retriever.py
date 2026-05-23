"""检索服务 — 向量检索 + 结果加工。"""

from server.services.embedder import Embedder


class Retriever:
    def __init__(self, vector_store, config: dict):
        self.store = vector_store
        self.embedder = Embedder(config)
        self.top_k = int(config.get("retrieval_top_k", "5"))

    def retrieve(self, query: str) -> list[dict]:
        hits = self.store.search(query, top_k=self.top_k)
        results = []
        for hit in hits:
            results.append({
                "chunk_id": hit["id"],
                "content": hit["content"],
                "score": hit.get("score", 0.0),
                "document_id": hit.get("metadata", {}).get("document_id", ""),
                "document_title": hit.get("metadata", {}).get("title", ""),
                "file_name": hit.get("metadata", {}).get("file_name", ""),
                "chunk_no": hit.get("metadata", {}).get("chunk_no", 0),
            })
        return results

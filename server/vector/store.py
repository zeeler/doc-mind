"""ChromaDB 向量存储封装。"""

import chromadb
from chromadb.config import Settings


class VectorStore:
    def __init__(self, persist_dir: str, collection_name: str = "knowledge_base"):
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def add(self, ids: list[str], texts: list[str], embeddings: list[list[float]] | None = None, metadatas: list[dict] | None = None) -> None:
        if embeddings:
            self.collection.add(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas or [{}] * len(ids))
        else:
            self.collection.add(ids=ids, documents=texts, metadatas=metadatas or [{}] * len(ids))

    def search(self, query: str, top_k: int = 5, where: dict | None = None) -> list[dict]:
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where,
        )
        hits = []
        ids_list = results.get("ids", [[]])[0]
        docs_list = results.get("documents", [[]])[0]
        metas_list = results.get("metadatas", [[]])[0]
        distances_list = results.get("distances", [[]])[0]
        for i in range(len(ids_list)):
            hit = {
                "id": ids_list[i],
                "content": docs_list[i] if i < len(docs_list) else "",
                "score": 1.0 - distances_list[i] if i < len(distances_list) else 0.0,
            }
            metadata = metas_list[i] if i < len(metas_list) else {}
            if metadata:
                hit.update(metadata)
            hit["metadata"] = metadata
            hits.append(hit)
        return hits

    def delete_by_document_id(self, document_id: str) -> None:
        existing = self.collection.get(where={"document_id": document_id})
        if existing and existing["ids"]:
            self.collection.delete(ids=existing["ids"])

    def count(self) -> int:
        return self.collection.count()

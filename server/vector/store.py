"""ChromaDB 向量存储封装。"""

import threading
import chromadb
from chromadb.config import Settings

# 全局共享的 PersistentClient 缓存，避免多个实例指向同一目录造成锁竞争
_clients: dict[str, chromadb.PersistentClient] = {}
_clients_lock = threading.Lock()


def _get_client(persist_dir: str) -> chromadb.PersistentClient:
    """获取或创建共享的 PersistentClient（线程安全）。"""
    with _clients_lock:
        if persist_dir not in _clients:
            _clients[persist_dir] = chromadb.PersistentClient(
                path=persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
        return _clients[persist_dir]


def get_client(persist_dir: str) -> chromadb.PersistentClient:
    """公开接口，供 memory_store 等模块使用。"""
    return _get_client(persist_dir)


class VectorStore:
    def __init__(self, persist_dir: str, collection_name: str = "knowledge_base"):
        self.client = _get_client(persist_dir)
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def add(self, ids: list[str], texts: list[str], embeddings: list[list[float]] | None = None, metadatas: list[dict] | None = None) -> None:
        if metadatas is None:
            metadatas = [{} for _ in ids]
        if embeddings:
            self.collection.add(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
        else:
            self.collection.add(ids=ids, documents=texts, metadatas=metadatas)

    def search(self, query: str, top_k: int = 5, where: dict | None = None, query_embeddings: list[list[float]] | None = None) -> list[dict]:
        query_kwargs: dict = {"n_results": top_k, "where": where}
        if query_embeddings:
            query_kwargs["query_embeddings"] = query_embeddings
        else:
            query_kwargs["query_texts"] = [query]
        results = self.collection.query(**query_kwargs)
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
            hit["metadata"] = metadata
            hits.append(hit)
        return hits

    def delete_by_document_id(self, document_id: str) -> None:
        existing = self.collection.get(where={"document_id": document_id})
        if existing and existing["ids"]:
            self.collection.delete(ids=existing["ids"])

    def count(self) -> int:
        return self.collection.count()

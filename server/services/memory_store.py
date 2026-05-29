"""ChromaDB 记忆存储封装。"""

import uuid
import chromadb
from chromadb.config import Settings


class MemoryStore:
    def __init__(self, persist_dir: str):
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(name="memories")

    def add(self, mem_id: str | None, content: str, metadata: dict) -> str:
        mid = mem_id or f"mem-{uuid.uuid4().hex[:12]}"
        self.collection.add(
            ids=[mid],
            documents=[content],
            metadatas=[metadata],
        )
        return mid

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        results = self.collection.query(query_texts=[query], n_results=top_k)
        hits = []
        ids_list = results.get("ids", [[]])[0]
        docs_list = results.get("documents", [[]])[0]
        metas_list = results.get("metadatas", [[]])[0]
        distances_list = results.get("distances", [[]])[0]
        for i in range(len(ids_list)):
            hits.append({
                "id": ids_list[i],
                "content": docs_list[i] if i < len(docs_list) else "",
                "metadata": metas_list[i] if i < len(metas_list) else {},
                "score": 1.0 - distances_list[i] if i < len(distances_list) else 0.0,
            })
        return hits

    def delete(self, mem_id: str) -> None:
        self.collection.delete(ids=[mem_id])

    def update(self, mem_id: str, content: str, metadata: dict) -> None:
        self.collection.update(ids=[mem_id], documents=[content], metadatas=[metadata])

    def count(self) -> int:
        return self.collection.count()

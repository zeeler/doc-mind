"""ChromaDB 记忆存储封装。"""

import uuid
from datetime import datetime, timezone, timedelta
from server.vector.store import get_client


class MemoryStore:
    def __init__(self, persist_dir: str):
        self.client = get_client(persist_dir)
        # 显式指定 cosine 空间，确保 distance 在 [0,2] 范围内，去重阈值 0.85 才能生效
        self.collection = self.client.get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, mem_id: str | None, content: str, metadata: dict, expire_days: int = 30) -> str:
        mid = mem_id or f"mem-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "type": metadata.get("type", "manual"),
            "scope": metadata.get("scope", "global"),
            "source_conv_id": metadata.get("source_conv_id", ""),
            "count": metadata.get("count", 1),
            "importance": metadata.get("importance", 0.5),
            "created_at": metadata.get("created_at", now),
            "updated_at": metadata.get("updated_at", now),
        }
        # session 级记忆设置过期
        if meta["scope"] == "session" and not metadata.get("expires_at"):
            expires = datetime.now(timezone.utc) + timedelta(days=expire_days)
            meta["expires_at"] = expires.timestamp()
        elif metadata.get("expires_at"):
            meta["expires_at"] = metadata["expires_at"]
        # 合并自定义 metadata（保留额外字段）
        for k, v in metadata.items():
            if k not in meta:
                meta[k] = v
        self.collection.add(
            ids=[mid],
            documents=[content],
            metadatas=[meta],
        )
        return mid

    def search(self, query: str, top_k: int = 5, scope: str | None = None,
               exclude_expired: bool = True) -> list[dict]:
        where_filter = None
        if scope:
            where_filter = {"scope": scope}
        # 过期过滤时多取一倍，补偿过滤后数量不足
        fetch_k = top_k * 2 if exclude_expired else top_k
        results = self.collection.query(
            query_texts=[query],
            n_results=fetch_k,
            where=where_filter,
        )
        hits = []
        now = datetime.now(timezone.utc).timestamp()
        ids_list = results.get("ids", [[]])[0]
        docs_list = results.get("documents", [[]])[0]
        metas_list = results.get("metadatas", [[]])[0]
        distances_list = results.get("distances", [[]])[0]
        for i in range(len(ids_list)):
            meta = metas_list[i] if i < len(metas_list) else {}
            # 过滤过期记忆
            if exclude_expired and meta.get("expires_at"):
                if meta["expires_at"] < now:
                    continue
            hits.append({
                "id": ids_list[i],
                "content": docs_list[i] if i < len(docs_list) else "",
                "metadata": meta,
                # cosine distance ∈ [0,2], 归一化到 [0,1]: score = 1 - distance/2
                "score": max(0.0, 1.0 - distances_list[i] / 2.0) if i < len(distances_list) else 0.0,
            })
        return hits[:top_k]

    def delete(self, mem_id: str) -> None:
        self.collection.delete(ids=[mem_id])

    def update(self, mem_id: str, content: str, metadata: dict) -> None:
        meta = dict(metadata)
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.collection.update(ids=[mem_id], documents=[content], metadatas=[meta])

    def count(self) -> int:
        return self.collection.count()

    def get_all(self, scope: str | None = None, limit: int = 100) -> list[dict]:
        """获取全部记忆（用于 consolidate 和 export）。"""
        where_filter = {"scope": scope} if scope else None
        results = self.collection.get(limit=limit, where=where_filter)
        memories = []
        if not results.get("ids"):
            return memories
        for i in range(len(results["ids"])):
            memories.append({
                "id": results["ids"][i],
                "content": results["documents"][i] if results.get("documents") else "",
                "metadata": results["metadatas"][i] if results.get("metadatas") else {},
            })
        return memories

    def delete_expired(self) -> int:
        """删除所有过期记忆，返回删除数。"""
        now = datetime.now(timezone.utc).timestamp()
        results = self.collection.get(where={"expires_at": {"$lt": now}})
        expired_ids = results.get("ids", [])
        if expired_ids:
            self.collection.delete(ids=expired_ids)
        return len(expired_ids)

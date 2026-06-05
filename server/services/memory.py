"""记忆服务 — 存入/搜索/去重/摘要。"""

import logging
import threading
from datetime import datetime, timezone
from server.services.memory_store import MemoryStore
from server.database import DATA_DIR, get_session_ctx

logger = logging.getLogger("knowledge-base")

MEMORY_DEDUP_THRESHOLD = 0.85
_store: MemoryStore | None = None
_store_lock = threading.Lock()


def _get_store() -> MemoryStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = MemoryStore(persist_dir=str(DATA_DIR / "chroma"))
    return _store


def add_memory(content: str, mem_type: str, metadata: dict | None = None) -> str:
    """存入记忆，自动去重。返回记忆 ID。"""
    store = _get_store()
    meta = dict(metadata or {})
    meta["type"] = mem_type
    meta.setdefault("count", 1)
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    meta.setdefault("created_at", meta["updated_at"])

    # 去重：检索相似记忆
    existing = store.search(content, top_k=3)
    for hit in existing:
        if hit["score"] >= MEMORY_DEDUP_THRESHOLD:
            old_meta = hit["metadata"]
            merged = f"{hit['content']}\n{content}"[:2000]
            old_meta["count"] = old_meta.get("count", 1) + 1
            old_meta["updated_at"] = meta["updated_at"]
            store.update(hit["id"], merged, old_meta)
            logger.info(f"记忆合并: {hit['id'][:12]} (count={old_meta['count']})")
            return hit["id"]

    return store.add(None, content, meta)


def search_memories(query: str, top_k: int = 5) -> list[dict]:
    """检索相关记忆。"""
    store = _get_store()
    return store.search(query, top_k=top_k)


def list_memories(mem_type: str = None, limit: int = 50) -> list[dict]:
    """列出记忆（按更新时间倒序）。"""
    store = _get_store()
    memories = store.get_all(limit=limit)
    if mem_type:
        memories = [m for m in memories if m["metadata"].get("type") == mem_type]
    # 将 type 提升到顶层以保持 API 一致
    result = []
    for m in memories:
        result.append({
            "id": m["id"],
            "content": m["content"],
            "type": m["metadata"].get("type", ""),
            "metadata": m["metadata"],
        })
    result.sort(key=lambda m: m["metadata"].get("updated_at", ""), reverse=True)
    return result[:limit]


def delete_memory(mem_id: str) -> None:
    store = _get_store()
    try:
        store.delete(mem_id)
    except Exception as e:
        logger.warning(f"删除记忆失败 {mem_id}: {e}")


def _reset_store() -> None:
    """重置 store 单例（仅测试用）。"""
    global _store
    _store = None


def summarize_conversation(conv_id: str) -> int:
    """对一段对话生成摘要记忆。返回新增的记忆数。"""
    from server.models.conversation import Conversation
    from server.services.llm import LLMAdapter
    from server.config import AppConfig

    with get_session_ctx() as session:
        conv = session.get(Conversation, conv_id)
        if not conv:
            return 0
        messages = [{"role": m.role, "content": m.content} for m in conv.messages]

    if len(messages) < 2:
        return 0

    config = AppConfig().get_all()
    llm = LLMAdapter(config)

    conversation_text = "\n".join(
        f"{m['role']}: {m['content'][:500]}" for m in messages[-20:]
    )

    prompt = f"""请从以下对话中提取关键信息，分为三类：

1. **偏好 (preference)**：用户的回答风格偏好、关注领域、工作要求
2. **结论 (conclusion)**：从对话中得出的分析结论或决策
3. **事实 (fact)**：用户明确陈述的事实信息

每条信息用一行，格式为 `[类型] 内容`。如果没有某类信息则跳过。只输出提取的信息，不要其他文字。

对话：
{conversation_text}
"""
    try:
        result = llm.chat(messages=[{"role": "user", "content": prompt}])
        text = result.get("content", "")
    except Exception as e:
        logger.error(f"摘要生成失败: {e}")
        return 0

    count = 0
    type_map = {"[偏好]": "preference", "[结论]": "conclusion", "[事实]": "fact"}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        for prefix, mem_type in type_map.items():
            if prefix in line:
                content = line.replace(prefix, "").strip()
                if content:
                    add_memory(content, mem_type, {"source_conv_id": conv_id})
                    count += 1
                break

    logger.info(f"对话摘要完成: conv={conv_id}, 记忆数={count}")
    return count

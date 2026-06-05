"""记忆服务 — thin wrapper，委托给 MemoryManager。"""

import logging
import threading
from server.services.memory_manager import MemoryManager
from server.database import DATA_DIR, get_session_ctx

logger = logging.getLogger("knowledge-base")

MEMORY_DEDUP_THRESHOLD = 0.85
_manager: MemoryManager | None = None
_manager_lock = threading.Lock()


def _get_manager(llm=None) -> MemoryManager:
    global _manager
    # Only use singleton when llm is None (non-LLM operations)
    if llm is None:
        if _manager is None:
            with _manager_lock:
                if _manager is None:
                    from server.config import AppConfig
                    config = AppConfig().get_all()
                    _manager = MemoryManager(
                        config=config, llm=None,
                        persist_dir=str(DATA_DIR / "chroma"),
                    )
        return _manager
    # For LLM operations, create a fresh manager with the provided llm
    from server.config import AppConfig
    config = AppConfig().get_all()
    return MemoryManager(config=config, llm=llm, persist_dir=str(DATA_DIR / "chroma"))


def add_memory(content: str, mem_type: str, metadata: dict | None = None) -> str:
    """存入记忆，自动去重。返回记忆 ID。"""
    mgr = _get_manager()
    meta = dict(metadata or {})
    return mgr.memorize(content, mem_type=mem_type, metadata=meta)


def search_memories(query: str, top_k: int = 5) -> list[dict]:
    """检索相关记忆。"""
    mgr = _get_manager()
    return mgr.recall(query, top_k=top_k)


def list_memories(mem_type: str = None, limit: int = 50) -> list[dict]:
    """列出记忆（按更新时间倒序）。"""
    mgr = _get_manager()
    store = mgr.store
    results = store.get_all(limit=limit * 2)  # 多取一些以过滤 type
    memories = []
    if not results:
        return memories
    for mem in results:
        mem_type_val = mem.get("metadata", {}).get("type", "")
        if mem_type and mem_type_val != mem_type:
            continue
        memories.append({
            "id": mem["id"],
            "content": mem["content"],
            "type": mem_type_val,
            "metadata": mem["metadata"],
        })
    memories.sort(key=lambda m: m["metadata"].get("updated_at", ""), reverse=True)
    return memories[:limit]


def delete_memory(mem_id: str) -> None:
    mgr = _get_manager()
    try:
        mgr.store.delete(mem_id)
    except Exception as e:
        logger.warning(f"删除记忆失败 {mem_id}: {e}")


def _reset_store() -> None:
    """重置单例（仅测试用）。"""
    global _manager
    _manager = None


def summarize_conversation(conv_id: str) -> int:
    """对一段对话生成摘要记忆（兼容层，委托给 MemoryManager.observe()）。"""
    from server.models.conversation import Conversation
    with get_session_ctx() as session:
        conv = session.get(Conversation, conv_id)
        if not conv:
            return 0
        messages = [{"role": m.role, "content": m.content} for m in conv.messages]

    if len(messages) < 2:
        return 0

    from server.config import AppConfig
    from server.services.llm import LLMAdapter
    config = AppConfig().get_all()
    llm = LLMAdapter(config)
    mgr = _get_manager(llm=llm)
    return mgr.observe(messages, conv_id)

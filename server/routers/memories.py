"""记忆管理路由。"""

from fastapi import APIRouter, HTTPException
from server.services.memory_manager import MemoryManager
from server.config import AppConfig
from server.services.llm import LLMAdapter

router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


def _get_mgr(with_llm: bool = False) -> MemoryManager:
    config = AppConfig().get_all()
    llm = LLMAdapter(config) if with_llm else None
    return MemoryManager(config=config, llm=llm)


# ====== 被动记忆（API 直存）======

@router.post("/remember")
def remember_message(body: dict):
    conversation_id = body.get("conversation_id")
    message_id = body.get("message_id")
    note = body.get("note", "").strip()
    scope = body.get("scope", "global")

    if not conversation_id or not message_id:
        raise HTTPException(status_code=400, detail="缺少 conversation_id 或 message_id")

    from server.database import get_session_ctx
    from server.models.conversation import Message
    with get_session_ctx() as session:
        msg = session.get(Message, message_id)
        if not msg:
            raise HTTPException(status_code=404, detail="消息不存在")
        content = msg.content
        if note:
            content = f"{content}\n备注: {note}"

    mgr = _get_mgr()
    mid = mgr.memorize(content, mem_type="manual", scope=scope,
                        metadata={"source_conv_id": conversation_id})
    return {"code": "OK", "data": {"id": mid}}


# ====== 列出记忆 ======

@router.get("")
def list_memories_endpoint(mem_type: str = None, scope: str = None, limit: int = 50):
    from server.services import memory as mem
    data = mem.list_memories(mem_type=mem_type, limit=limit)
    if scope:
        data = [m for m in data if m.get("metadata", {}).get("scope") == scope]
    return {"code": "OK", "data": data}


# ====== 搜索记忆 ======

@router.get("/search")
def search_memories_endpoint(q: str = "", scope: str = None, top_k: int = 5):
    if not q:
        raise HTTPException(status_code=400, detail="缺少查询参数 q")
    mgr = _get_mgr()
    results = mgr.recall(q, top_k=top_k)
    if scope:
        results = [r for r in results if r.get("metadata", {}).get("scope") == scope]
    return {"code": "OK", "data": results}


# ====== 删除记忆 ======

@router.delete("/{mem_id}")
def delete_memory_endpoint(mem_id: str):
    from server.services import memory as mem
    mem.delete_memory(mem_id)
    return {"code": "OK", "data": None}


# ====== 主动触发分析 ======

@router.post("/observe")
def observe_endpoint(body: dict):
    conversation_id = body.get("conversation_id")
    if not conversation_id:
        raise HTTPException(status_code=400, detail="缺少 conversation_id")

    from server.database import get_session_ctx
    from server.models.conversation import Conversation
    with get_session_ctx() as session:
        conv = session.get(Conversation, conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在")
        messages = [{"role": m.role, "content": m.content} for m in conv.messages]

    mgr = _get_mgr(with_llm=True)
    count = mgr.observe(messages, conversation_id)
    return {"code": "OK", "data": {"new_memories": count}}


# ====== 合并记忆 ======

@router.post("/consolidate")
def consolidate_endpoint(body: dict = {}):
    dry_run = body.get("dry_run", False)
    mgr = _get_mgr()
    result = mgr.consolidate(dry_run=dry_run)
    return {"code": "OK", "data": result}


# ====== 导出 md ======

@router.post("/export")
def export_memories_endpoint(body: dict = {}):
    scope = body.get("scope", None)
    mgr = _get_mgr()
    path = mgr.export_md(scope=scope)
    files = mgr.exporter.get_export_files() if mgr.exporter else []
    return {"code": "OK", "data": {"path": str(path), "files": len(files)}}


@router.get("/export")
def get_export_files_endpoint():
    mgr = _get_mgr()
    files = mgr.exporter.get_export_files() if mgr.exporter else []
    return {"code": "OK", "data": {"files": files}}

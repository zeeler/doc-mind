"""记忆管理路由。"""

from fastapi import APIRouter, HTTPException
from server.services import memory as mem

router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


@router.post("/remember")
def remember_message(body: dict):
    conversation_id = body.get("conversation_id")
    message_id = body.get("message_id")
    note = body.get("note", "").strip()

    if not conversation_id or not message_id:
        raise HTTPException(status_code=400, detail="缺少 conversation_id 或 message_id")

    from server.database import get_session
    from server.models.conversation import Message
    with next(get_session()) as session:
        msg = session.get(Message, message_id)
        if not msg:
            raise HTTPException(status_code=404, detail="消息不存在")
        if msg.role == "user":
            content = f"用户: {msg.content}"
        else:
            content = msg.content
        if note:
            content = f"{content}\n备注: {note}"

    mid = mem.add_memory(content, "manual", {"source_conv_id": conversation_id})
    return {"code": "OK", "data": {"id": mid}}


@router.get("")
def list_memories_endpoint(mem_type: str = None, limit: int = 50):
    data = mem.list_memories(mem_type=mem_type, limit=limit)
    return {"code": "OK", "data": data}


@router.get("/search")
def search_memories_endpoint(q: str = "", top_k: int = 5):
    if not q:
        raise HTTPException(status_code=400, detail="缺少查询参数 q")
    results = mem.search_memories(q, top_k=top_k)
    return {"code": "OK", "data": results}


@router.delete("/{mem_id}")
def delete_memory_endpoint(mem_id: str):
    mem.delete_memory(mem_id)
    return {"code": "OK", "data": None}

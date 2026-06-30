"""对话路由 — 同步问答 + SSE 流式问答。"""

import json
import logging
import uuid
from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse
from server.database import get_session, get_session_ctx, DATA_DIR
from server.models.conversation import Conversation, Message

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def _get_conversation_history(session: Session, conversation_id: str, limit: int = 6) -> list[dict]:
    """获取当前对话的最近 N 条消息作为上下文历史。"""
    from server.models.conversation import Message
    recent = (
        session.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
        .all()
    )
    # 按时间正序返回
    return [
        {"role": m.role, "content": m.content}
        for m in reversed(recent)
    ]


def _recall_memory_context(question: str, conversation_id: str | None) -> str:
    """搜索相关记忆并返回可注入 system prompt 的文本。"""
    try:
        from server.config import AppConfig as Cfg
        cfg = Cfg().get_all()
        if cfg.get("memory_enabled", "true") != "true":
            return ""
        from server.services.memory_manager import MemoryManager
        mem_mgr = MemoryManager.get_singleton()
        return mem_mgr.recall_as_context(question, conv_id=conversation_id)
    except Exception as e:
        logger.warning(f"记忆召回失败: {e}")
        return ""


@router.post("/ask")
def chat_ask(body: dict, session: Session = Depends(get_session)):
    conversation_id = body.get("conversation_id")
    question = body.get("question", "").strip()

    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    conv = session.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")

    user_msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        role="user",
        content=question,
    )
    session.add(user_msg)

    if conv.title == "新会话":
        conv.title = question[:50] + ("..." if len(question) > 50 else "")

    try:
        from server.services.registry import ServiceRegistry
        rag = ServiceRegistry.get_singleton().get_rag_service(DATA_DIR)
        history = _get_conversation_history(session, conversation_id)

        memory_context = _recall_memory_context(question, conversation_id)

        result = rag.ask_sync(question, history=history, memory_context=memory_context)
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}", exc_info=True)
        session.commit()
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {str(e)}")

    assistant_msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        role="assistant",
        content=result["answer"],
        citations_json=result["citations"],
    )
    session.add(assistant_msg)
    session.commit()

    from server.services.observer import get_observe_executor, run_observe_bg
    get_observe_executor().submit(run_observe_bg, conversation_id, history, question, result["answer"])

    return {
        "code": "OK",
        "message": "success",
        "data": {
            "message_id": assistant_msg.id,
            "answer": result["answer"],
            "citations": result["citations"],
        },
    }


@router.post("/stream")
async def chat_stream(body: dict, request: Request, session: Session = Depends(get_session)):
    conversation_id = body.get("conversation_id")
    question = body.get("question", "").strip()

    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    conv = session.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")

    user_msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        role="user",
        content=question,
    )
    session.add(user_msg)
    if conv.title == "新会话":
        conv.title = question[:50] + ("..." if len(question) > 50 else "")
    session.commit()

    try:
        from server.services.registry import ServiceRegistry
        rag = ServiceRegistry.get_singleton().get_rag_service(DATA_DIR)
        history = _get_conversation_history(session, conversation_id)

        memory_context = _recall_memory_context(question, conversation_id)
    except Exception as e:
        logger.error(f"RAG 服务初始化失败: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"RAG 服务初始化失败: {str(e)}")

    async def event_stream():
        full_answer = ""
        citations = []
        try:
            yield {"event": "meta", "data": json.dumps({"conversation_id": conversation_id}, ensure_ascii=False)}
            async for chunk in rag.ask_stream(question, history=history, memory_context=memory_context):
                # 客户端断开连接时提前终止，避免浪费 LLM 资源
                if await request.is_disconnected():
                    logger.info(f"客户端断开连接，终止流式生成 conv={conversation_id}")
                    break
                if chunk["type"] == "token":
                    full_answer += chunk["content"]
                    yield {"data": json.dumps({"type": "token", "content": chunk["content"]}, ensure_ascii=False)}
                elif chunk["type"] == "citations":
                    citations = chunk["data"]
                    yield {"event": "citations", "data": json.dumps(citations, ensure_ascii=False)}
                elif chunk["type"] == "done":
                    pass
        except Exception as e:
            logger.error(f"LLM 流式调用失败: {e}", exc_info=True)
            yield {"event": "error", "data": json.dumps({"message": f"LLM 调用失败: {str(e)}"}, ensure_ascii=False)}
        finally:
            # 只有实际收到回复内容时才保存消息，避免流式完全失败时产生空消息
            if full_answer:
                with get_session_ctx() as s:
                    assistant_msg = Message(
                        id=str(uuid.uuid4()),
                        conversation_id=conversation_id,
                        role="assistant",
                        content=full_answer,
                        citations_json=citations,
                    )
                    s.add(assistant_msg)
                    s.commit()
                from server.services.observer import get_observe_executor, run_observe_bg
                get_observe_executor().submit(run_observe_bg, conversation_id, history, question, full_answer)
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_stream())

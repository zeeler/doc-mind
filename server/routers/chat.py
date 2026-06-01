"""对话路由 — 同步问答 + SSE 流式问答。"""

import json
import logging
import uuid
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse
from server.database import get_session, get_session_ctx, DATA_DIR
from server.models.conversation import Conversation, Message
from server.config import AppConfig
from server.services.rag import RAGService
from server.services.retriever import Retriever
from server.vector.store import VectorStore

logger = logging.getLogger("knowledge-base")
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

_rag_service_cache: RAGService | None = None
_rag_cache_key: tuple | None = None


def _get_rag_service(data_dir):
    """获取缓存的 RAGService 实例，配置变化时重建。"""
    global _rag_service_cache, _rag_cache_key
    cfg = AppConfig()
    config = cfg.get_all()
    # 仅根据影响检索的配置项判断是否需要重建
    cache_key = (
        str(data_dir),
        config.get("retrieval_top_k", "15"),
        config.get("retrieval_enable_mmr", "true"),
        config.get("retrieval_mmr_lambda", "0.7"),
        config.get("retrieval_fetch_multiplier", "3"),
        config.get("retrieval_enable_query_expansion", "false"),
        config.get("retrieval_context_window", "2"),
        config.get("retrieval_max_results", "50"),
    )
    if _rag_service_cache is None or cache_key != _rag_cache_key:
        store = VectorStore(persist_dir=str(data_dir / "chroma"))
        retriever = Retriever(vector_store=store, config=config)
        _rag_service_cache = RAGService(retriever=retriever, config=config)
        _rag_cache_key = cache_key
    return _rag_service_cache


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
        rag = _get_rag_service(DATA_DIR)
        result = rag.ask_sync(question)
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
async def chat_stream(body: dict, session: Session = Depends(get_session)):
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
        rag = _get_rag_service(DATA_DIR)
    except Exception as e:
        logger.error(f"RAG 服务初始化失败: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"RAG 服务初始化失败: {str(e)}")

    async def event_stream():
        full_answer = ""
        citations = []
        try:
            yield {"event": "meta", "data": json.dumps({"conversation_id": conversation_id}, ensure_ascii=False)}
            async for chunk in rag.ask_stream(question):
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
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_stream())

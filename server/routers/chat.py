"""对话路由 — 同步问答 + SSE 流式问答。"""

import json
import logging
import threading
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

_rag_service_cache: RAGService | None = None
_rag_cache_key: tuple | None = None
_rag_cache_lock = threading.Lock()


def _get_rag_service(data_dir):
    """获取缓存的 RAGService 实例，配置变化时重建（线程安全）。"""
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
        config.get("web_search_enabled", "false"),
        config.get("tavily_api_key", ""),
        config.get("web_search_max_results", "5"),
        config.get("embedding_enabled", "false"),
        config.get("embedding_model", ""),
        config.get("embedding_api_base", ""),
        config.get("embedding_api_key", ""),
    )
    # 双重检查锁定模式避免并发重建
    if _rag_service_cache is not None and cache_key == _rag_cache_key:
        return _rag_service_cache
    with _rag_cache_lock:
        if _rag_service_cache is None or cache_key != _rag_cache_key:
            retriever = Retriever(config=config)
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
        history = _get_conversation_history(session, conversation_id)

        # === 记忆召回 ===
        memory_context = ""
        try:
            from server.config import AppConfig as Cfg
            cfg = Cfg().get_all()
            if cfg.get("memory_enabled", "true") == "true":
                from server.services.memory_manager import MemoryManager
                from server.services.llm import LLMAdapter
                mem_mgr = MemoryManager(config=cfg, llm=LLMAdapter(cfg))
                memory_context = mem_mgr.recall_as_context(question, conv_id=conversation_id)
        except Exception as e:
            logger.warning(f"记忆召回失败: {e}")

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

    # 后台异步：主动记忆分析
    def _observe_bg():
        try:
            from server.config import AppConfig as Cfg
            cfg = Cfg().get_all()
            if cfg.get("memory_enabled", "true") != "true":
                return
            if cfg.get("memory_auto_observe", "true") != "true":
                return
            observe_interval = int(cfg.get("memory_observe_interval", "3"))
            from server.database import get_session_ctx as ctx
            from server.models.conversation import Message as Msg
            with ctx() as s:
                msg_count = s.query(Msg).filter(
                    Msg.conversation_id == conversation_id
                ).count()
            if msg_count % observe_interval != 0:
                return
            from server.services.memory_manager import MemoryManager
            from server.services.llm import LLMAdapter
            mem_mgr = MemoryManager(config=cfg, llm=LLMAdapter(cfg))
            recent = history + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": result["answer"]},
            ]
            mem_mgr.observe(recent, conversation_id)
        except Exception as e:
            logger.warning(f"主动记忆后台任务失败: {e}")
    threading.Thread(target=_observe_bg, daemon=True).start()

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
        history = _get_conversation_history(session, conversation_id)

        # === 记忆召回 ===
        memory_context = ""
        try:
            from server.config import AppConfig as Cfg
            cfg = Cfg().get_all()
            if cfg.get("memory_enabled", "true") == "true":
                from server.services.memory_manager import MemoryManager
                from server.services.llm import LLMAdapter
                mem_mgr = MemoryManager(config=cfg, llm=LLMAdapter(cfg))
                memory_context = mem_mgr.recall_as_context(question, conv_id=conversation_id)
        except Exception as e:
            logger.warning(f"记忆召回失败: {e}")
    except Exception as e:
        logger.error(f"RAG 服务初始化失败: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"RAG 服务初始化失败: {str(e)}")

    async def event_stream():
        full_answer = ""
        citations = []
        try:
            yield {"event": "meta", "data": json.dumps({"conversation_id": conversation_id}, ensure_ascii=False)}
            async for chunk in rag.ask_stream(question, history=history, memory_context=memory_context):
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
                # 后台异步：主动记忆分析
                def _observe_bg():
                    try:
                        from server.config import AppConfig as Cfg
                        cfg = Cfg().get_all()
                        if cfg.get("memory_enabled", "true") != "true":
                            return
                        if cfg.get("memory_auto_observe", "true") != "true":
                            return
                        observe_interval = int(cfg.get("memory_observe_interval", "3"))
                        from server.database import get_session_ctx as ctx
                        from server.models.conversation import Message as Msg
                        with ctx() as s:
                            msg_count = s.query(Msg).filter(
                                Msg.conversation_id == conversation_id
                            ).count()
                        if msg_count % observe_interval != 0:
                            return
                        from server.services.memory_manager import MemoryManager
                        from server.services.llm import LLMAdapter
                        mem_mgr = MemoryManager(config=cfg, llm=LLMAdapter(cfg))
                        recent = history + [
                            {"role": "user", "content": question},
                            {"role": "assistant", "content": full_answer},
                        ]
                        mem_mgr.observe(recent, conversation_id)
                    except Exception as e:
                        logger.warning(f"主动记忆后台任务失败: {e}")
                threading.Thread(target=_observe_bg, daemon=True).start()
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_stream())

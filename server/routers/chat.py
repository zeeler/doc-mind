"""对话路由 — 同步问答 + SSE 流式问答。"""

import json
import logging
import threading
import time
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

_cached_llm: 'LLMAdapter | None' = None
_cached_llm_config_key: tuple | None = None

_conv_last_observe: dict[str, float] = {}  # conv_id -> last_observe_timestamp
_conv_observing: set[str] = set()  # conv_id 正在执行 observe，避免并发重复
_conv_observe_lock = threading.Lock()
_llm_cache_lock = threading.Lock()
_observe_executor = None
_observe_executor_lock = threading.Lock()


def _get_cached_llm(config: dict):
    global _cached_llm, _cached_llm_config_key
    from server.services.llm import LLMAdapter
    key = (config.get("llm_provider"), config.get("mlx_chat_model"), config.get("openai_chat_model"),
           config.get("claude_chat_model"), config.get("custom_chat_model"))
    if _cached_llm is not None and key == _cached_llm_config_key:
        return _cached_llm
    with _llm_cache_lock:
        if _cached_llm is None or key != _cached_llm_config_key:
            _cached_llm = LLMAdapter(config)
            _cached_llm_config_key = key
        return _cached_llm


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


def _get_observe_executor():
    global _observe_executor
    if _observe_executor is None:
        with _observe_executor_lock:
            if _observe_executor is None:
                from concurrent.futures import ThreadPoolExecutor
                _observe_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="observe")
    return _observe_executor


def _recall_memory_context(question: str, conversation_id: str | None) -> str:
    """搜索相关记忆并返回可注入 system prompt 的文本。"""
    try:
        from server.config import AppConfig as Cfg
        cfg = Cfg().get_all()
        if cfg.get("memory_enabled", "true") != "true":
            return ""
        from server.services.memory_manager import MemoryManager
        mem_mgr = MemoryManager(config=cfg, llm=None)  # recall doesn't need LLM
        return mem_mgr.recall_as_context(question, conv_id=conversation_id)
    except Exception as e:
        import logging
        logger = logging.getLogger("knowledge-base")
        logger.warning(f"记忆召回失败: {e}")
        return ""


def _run_observe_bg(conversation_id: str, history: list[dict], question: str,
                    answer_text: str) -> None:
    """后台异步：主动记忆分析（供 chat_ask 和 chat_stream 共用）。"""
    # 防止同一会话的 observe 并发执行
    with _conv_observe_lock:
        if conversation_id in _conv_observing:
            return
        _conv_observing.add(conversation_id)
    try:
        from server.config import AppConfig as Cfg
        cfg = Cfg().get_all()
        if cfg.get("memory_enabled", "true") != "true":
            return
        if cfg.get("memory_auto_observe", "true") != "true":
            return

        idle_timeout = int(cfg.get("memory_session_idle_timeout", "30")) * 60  # 转为秒
        with _conv_observe_lock:
            last_obs = _conv_last_observe.get(conversation_id, 0)
        idle_secs = time.time() - last_obs

        if idle_secs > idle_timeout:
            from server.database import get_session_ctx as ctx
            from server.models.conversation import Message as Msg
            with ctx() as s:
                all_msgs = s.query(Msg).filter(
                    Msg.conversation_id == conversation_id
                ).order_by(Msg.created_at.asc()).all()
                if not all_msgs:
                    # 会话已删除，清理追踪状态
                    with _conv_observe_lock:
                        _conv_last_observe.pop(conversation_id, None)
                    return
                if len(all_msgs) >= 2:
                    from server.services.memory_manager import MemoryManager
                    messages = [{"role": m.role, "content": m.content} for m in all_msgs]
                    mem_mgr = MemoryManager(config=cfg, llm=_get_cached_llm(cfg))
                    mem_mgr.observe(messages, conversation_id)
            with _conv_observe_lock:
                _conv_last_observe[conversation_id] = time.time()
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
        mem_mgr = MemoryManager(config=cfg, llm=_get_cached_llm(cfg))
        recent = history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer_text},
        ]
        mem_mgr.observe(recent, conversation_id)
        with _conv_observe_lock:
            _conv_last_observe[conversation_id] = time.time()
    except Exception as e:
        import logging
        logger = logging.getLogger("knowledge-base")
        logger.warning(f"主动记忆后台任务失败: {e}")
    finally:
        with _conv_observe_lock:
            _conv_observing.discard(conversation_id)


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

    _get_observe_executor().submit(_run_observe_bg, conversation_id, history, question, result["answer"])

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
                _get_observe_executor().submit(_run_observe_bg, conversation_id, history, question, full_answer)
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_stream())

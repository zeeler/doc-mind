"""会话观察器 — 后台异步分析对话，主动发现并存储记忆。

供 chat.py 路由使用，避免在路由文件中维护复杂的后台线程状态。
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

# 会话级 observe 状态追踪
_conv_last_observe: dict[str, float] = {}  # conv_id -> last_observe_timestamp
_conv_observing: set[str] = set()  # conv_id 正在执行 observe，避免并发重复
_conv_observe_lock = threading.Lock()

# 后台 observe 线程池
_observe_executor = None
_observe_executor_lock = threading.Lock()


def get_observe_executor():
    """获取 observe 后台线程池（延迟初始化，线程安全）。"""
    global _observe_executor
    if _observe_executor is None:
        with _observe_executor_lock:
            if _observe_executor is None:
                from concurrent.futures import ThreadPoolExecutor
                _observe_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="observe")
    return _observe_executor


def shutdown_observe_executor():
    """应用退出时关闭 observe 线程池，避免 daemon 线程被强制终止。"""
    global _observe_executor
    if _observe_executor is not None:
        _observe_executor.shutdown(wait=True)
        _observe_executor = None


def forget_conversation(conversation_id: str) -> None:
    """会话删除时清理 observe 状态，防止 _conv_last_observe 无限增长。"""
    with _conv_observe_lock:
        _conv_last_observe.pop(conversation_id, None)
        _conv_observing.discard(conversation_id)


def run_observe_bg(conversation_id: str, history: list[dict], question: str,
                   answer_text: str) -> None:
    """后台异步：主动记忆分析（供 chat_ask 和 chat_stream 共用）。

    防止同一会话的 observe 并发执行；根据配置决定触发时机（空闲超时 / 消息数间隔）。
    """
    with _conv_observe_lock:
        if conversation_id in _conv_observing:
            return
        _conv_observing.add(conversation_id)
    try:
        from server.config import AppConfig
        cfg = AppConfig().get_all()
        if cfg.get("memory_enabled", "true") != "true":
            return
        if cfg.get("memory_auto_observe", "true") != "true":
            return

        idle_timeout = int(cfg.get("memory_session_idle_timeout", "30")) * 60  # 转为秒
        with _conv_observe_lock:
            last_obs = _conv_last_observe.get(conversation_id)
        if last_obs is None:
            # 首次交互：只登记时间戳并走消息数间隔逻辑，避免每个新会话第一轮就全量 observe
            with _conv_observe_lock:
                _conv_last_observe[conversation_id] = time.time()
            idle_secs = 0.0
        else:
            idle_secs = time.time() - last_obs

        if idle_secs > idle_timeout:
            from server.database import get_session_ctx
            from server.models.conversation import Message
            with get_session_ctx() as s:
                all_msgs = s.query(Message).filter(
                    Message.conversation_id == conversation_id
                ).order_by(Message.created_at.asc()).all()
                if not all_msgs:
                    with _conv_observe_lock:
                        _conv_last_observe.pop(conversation_id, None)
                    return
                if len(all_msgs) >= 2:
                    from server.services.memory_manager import MemoryManager
                    from server.services.registry import ServiceRegistry
                    messages = [{"role": m.role, "content": m.content} for m in all_msgs]
                    mem_mgr = MemoryManager.create_with_llm(llm=ServiceRegistry.get_singleton().get_llm())
                    mem_mgr.observe(messages, conversation_id)
            with _conv_observe_lock:
                _conv_last_observe[conversation_id] = time.time()
            return

        observe_interval = int(cfg.get("memory_observe_interval", "3"))
        from server.database import get_session_ctx
        from server.models.conversation import Message
        with get_session_ctx() as s:
            msg_count = s.query(Message).filter(
                Message.conversation_id == conversation_id
            ).count()
        if msg_count % observe_interval != 0:
            return

        from server.services.memory_manager import MemoryManager
        from server.services.registry import ServiceRegistry
        mem_mgr = MemoryManager.create_with_llm(llm=ServiceRegistry.get_singleton().get_llm())
        recent = history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer_text},
        ]
        mem_mgr.observe(recent, conversation_id)
        with _conv_observe_lock:
            _conv_last_observe[conversation_id] = time.time()
    except Exception as e:
        logger.warning(f"主动记忆后台任务失败: {e}")
    finally:
        with _conv_observe_lock:
            _conv_observing.discard(conversation_id)

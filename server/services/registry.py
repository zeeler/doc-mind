"""服务注册中心 — 统一管理 LLM/Embedder/Reranker/Search 等实例缓存。

各模块通过此类获取服务实例，避免重复创建和分散的缓存逻辑。
配置变更时自动检测并重建受影响的实例。
"""

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_singleton: 'ServiceRegistry | None' = None
_singleton_lock = threading.Lock()


class ServiceRegistry:
    """统一服务实例管理器（线程安全单例）。

    用法:
        reg = ServiceRegistry.get_singleton()
        llm = reg.get_llm()
        rag = reg.get_rag_service(data_dir)
    """

    def __init__(self):
        self._llm: 'LLMAdapter | None' = None
        self._llm_key: tuple | None = None
        self._llm_lock = threading.Lock()

        self._rag: 'RAGService | None' = None
        self._rag_key: tuple | None = None
        self._rag_lock = threading.Lock()

        self._search: 'SearchService | None' = None
        self._search_key: tuple | None = None
        self._search_lock = threading.Lock()

        self._embedder: 'Embedder | None' = None
        self._embedder_key: tuple | None = None
        self._embedder_lock = threading.Lock()

        self._reranker: 'Reranker | None' = None
        self._reranker_key: tuple | None = None
        self._reranker_lock = threading.Lock()

    # ====== LLMAdapter ======

    def get_llm(self) -> 'LLMAdapter':
        """获取缓存的 LLMAdapter，配置变更时自动重建。"""
        from server.config import AppConfig
        config = AppConfig().get_all()
        key = (
            config.get("llm_provider"),
            config.get("mlx_chat_model"),
            config.get("openai_chat_model"),
            config.get("claude_chat_model"),
            config.get("custom_chat_model"),
            config.get("mlx_api_base"),
            config.get("openai_api_base"),
            config.get("custom_api_base"),
            config.get("custom_api_type"),
        )
        if self._llm is not None and key == self._llm_key:
            return self._llm
        with self._llm_lock:
            if self._llm is None or key != self._llm_key:
                from server.services.llm import LLMAdapter
                self._llm = LLMAdapter(config)
                self._llm_key = key
        return self._llm

    # ====== Embedder ======

    def get_embedder(self) -> 'Embedder | None':
        """获取缓存的 Embedder（无独立配置时返回 None）。"""
        from server.config import AppConfig, has_embedding_model
        config = AppConfig().get_all()
        if not has_embedding_model(config):
            return None
        key = (
            config.get("embedding_enabled"),
            config.get("embedding_model"),
            config.get("embedding_api_base"),
            config.get("embedding_api_key"),
        )
        if self._embedder is not None and key == self._embedder_key:
            return self._embedder
        with self._embedder_lock:
            if self._embedder is None or key != self._embedder_key:
                from server.services.embedder import Embedder
                self._embedder = Embedder(config)
                self._embedder_key = key
        return self._embedder

    # ====== Reranker ======

    def get_reranker(self) -> 'Reranker | None':
        """获取缓存的 Reranker（未启用时返回 None）。"""
        from server.config import AppConfig, has_reranker_model
        config = AppConfig().get_all()
        if not has_reranker_model(config):
            return None
        key = (
            config.get("reranker_model"),
            config.get("reranker_api_base"),
            config.get("reranker_api_key"),
        )
        if self._reranker is not None and key == self._reranker_key:
            return self._reranker
        with self._reranker_lock:
            if self._reranker is None or key != self._reranker_key:
                from server.services.reranker import Reranker
                self._reranker = Reranker(config)
                self._reranker_key = key
        return self._reranker

    # ====== RAGService ======

    def get_rag_service(self, data_dir: Path) -> 'RAGService':
        """获取缓存的 RAGService，配置变更时自动重建。"""
        from server.config import AppConfig
        config = AppConfig().get_all()
        key = (
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
        if self._rag is not None and key == self._rag_key:
            return self._rag
        with self._rag_lock:
            if self._rag is None or key != self._rag_key:
                from server.services.retriever import Retriever
                from server.services.rag import RAGService
                retriever = Retriever(config=config)
                self._rag = RAGService(retriever=retriever, config=config)
                self._rag_key = key
        return self._rag

    # ====== SearchService ======

    def get_search_service(self, data_dir: Path, top_k: int = 10) -> 'SearchService':
        """获取缓存的 SearchService，配置变更时自动重建。"""
        from server.services.search import SearchService
        key = (str(data_dir), top_k)
        if self._search is not None and key == self._search_key:
            return self._search
        with self._search_lock:
            if self._search is None or key != self._search_key:
                self._search = SearchService(data_dir=data_dir, top_k=top_k)
                self._search_key = key
        return self._search

    # ====== 单例管理 ======

    @classmethod
    def get_singleton(cls) -> 'ServiceRegistry':
        """获取全局单例（线程安全）。"""
        global _singleton
        if _singleton is None:
            with _singleton_lock:
                if _singleton is None:
                    _singleton = cls()
        return _singleton

    @classmethod
    def reset_singleton(cls) -> None:
        """重置单例（仅测试用）。"""
        global _singleton
        _singleton = None

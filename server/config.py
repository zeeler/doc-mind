"""配置管理 — KV 存储在 SQLite app_config 表中（含 TTL 内存缓存）。"""

import threading
import time
from sqlalchemy import String, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from server.database import get_session_ctx, DATA_DIR  # DATA_DIR 被测试 monkeypatch 引用
from server.models.base import Base

# 配置缓存（减少频繁 SQLite 查询）
_cache: dict | None = None
_cache_time: float = 0.0
_cache_ttl: float = 5.0  # 5 秒 TTL
_cache_lock = threading.Lock()


class AppConfigModel(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


DEFAULTS = {
    "llm_provider": "mlx",           # "mlx" | "openai" | "claude" | "custom"
    "mlx_chat_model": "",
    "mlx_embedding_model": "",
    "mlx_api_base": "http://localhost:8080/v1",
    "openai_api_key": "",
    "openai_chat_model": "gpt-4o-mini",
    "openai_embedding_model": "text-embedding-3-small",
    "openai_api_base": "https://api.openai.com/v1",
    "claude_api_key": "",
    "claude_chat_model": "claude-sonnet-4-6",
    "custom_api_base": "",
    "custom_api_key": "",
    "custom_chat_model": "",
    "custom_embedding_model": "",
    "custom_api_type": "openai",     # "openai" | "anthropic" — API 格式
    "ocr_enabled": "true",
    "ocr_engine": "tesseract",       # "tesseract" | "ollama" — OCR 引擎
    "ocr_ollama_model": "llama3.2-vision:11b",
    "ocr_ollama_base_url": "http://localhost:11434/v1",
    "ocr_max_workers": "2",
    "chunk_size": "800",
    "chunk_overlap": "100",
    "retrieval_top_k": "15",
    "retrieval_enable_mmr": "true",
    "retrieval_mmr_lambda": "0.7",
    "retrieval_rrf_alpha": "0.5",
    "retrieval_fetch_multiplier": "3",
    "retrieval_enable_query_expansion": "true",
    "retrieval_context_window": "3",
    "retrieval_max_results": "15",
    "chunk_structure_aware": "true",
    "web_search_enabled": "false",
    "tavily_api_key": "",
    "web_search_max_results": "5",
    "embedding_enabled": "false",
    "embedding_model": "",
    "embedding_api_base": "",
    "embedding_api_key": "",
    "reranker_enabled": "false",
    "reranker_model": "",
    "reranker_api_base": "",
    "reranker_api_key": "",
    "reranker_top_k": "3",
    # 记忆系统配置
    "memory_enabled": "true",
    "memory_auto_observe": "true",
    "memory_observe_interval": "3",
    "memory_recall_top_k": "5",
    "memory_dedup_threshold": "0.85",
    "memory_export_auto": "true",
    "memory_export_dir": "",
    "memory_session_idle_timeout": "30",
    "memory_session_expire_days": "30",
    "auto_tag_enabled": "true",
}


EMBEDDING_CONFIG_KEYS = (
    "custom_embedding_model", "openai_embedding_model", "mlx_embedding_model",
    "embedding_model",  # 独立 embedding 配置
)


def has_embedding_model(config: dict) -> bool:
    """判断配置中是否启用了外部 embedding 模型。"""
    return config.get("embedding_enabled") == "true" or any(config.get(k) for k in EMBEDDING_CONFIG_KEYS)


def has_reranker_model(config: dict) -> bool:
    """判断配置中是否启用了 Reranker 模型。"""
    return (
        config.get("reranker_enabled") == "true"
        and bool(config.get("reranker_model", "").strip())
        and bool(config.get("reranker_api_base", "").strip())
    )


class AppConfig:
    """运行时配置，读写 app_config 表。"""

    def get(self, key: str) -> str:
        return self.get_all().get(key, "")

    def set(self, key: str, value: str) -> None:
        global _cache, _cache_time
        with get_session_ctx() as session:
            row = session.get(AppConfigModel, key)
            if row is None:
                row = AppConfigModel(key=key, value={"v": value}, updated_at=datetime.now(timezone.utc))
                session.add(row)
            else:
                row.value = {"v": value}
                row.updated_at = datetime.now(timezone.utc)
            session.commit()
        # 写入后立即失效缓存
        with _cache_lock:
            _cache = None

    def get_all(self) -> dict:
        global _cache, _cache_time
        now = time.time()
        with _cache_lock:
            if _cache is not None and (now - _cache_time) < _cache_ttl:
                return dict(_cache)
        result = dict(DEFAULTS)
        with get_session_ctx() as session:
            rows = session.query(AppConfigModel).all()
            for row in rows:
                result[row.key] = str(row.value.get("v", DEFAULTS.get(row.key, "")))
        with _cache_lock:
            _cache = result
            _cache_time = now
        return dict(result)

    @staticmethod
    def invalidate_cache() -> None:
        """强制失效配置缓存（测试用）。"""
        global _cache
        with _cache_lock:
            _cache = None

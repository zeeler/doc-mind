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
    "api_key": "",                     # API 认证密钥（空=无认证，兼容旧行为）
    "llm_provider": "mlx",           # "mlx" | "openai" | "claude" | "custom"
    "llm_api_key": "",               # 统一 LLM API Key（所有 provider 共用，覆盖专用 key）
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
    "ocr_api_key": "",               # OCR API Key（为空时使用 llm_api_key）
    "ocr_max_workers": "2",
    "chunk_size": "800",
    "chunk_overlap": "100",
    "chunk_structure_aware": "true",
    "retrieval_top_k": "15",
    "retrieval_enable_mmr": "true",
    "retrieval_mmr_lambda": "0.7",
    "retrieval_rrf_alpha": "0.5",
    "retrieval_fetch_multiplier": "3",
    "retrieval_enable_query_expansion": "true",
    "retrieval_context_window": "3",
    "retrieval_max_results": "15",
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
        global _cache
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


class TypedConfig:
    """类型化配置访问器 — 将字符串值转换为正确类型。

    用法:
        cfg = TypedConfig()           # 从缓存读取
        k = cfg.retrieval_top_k       # int
        b = cfg.memory_enabled        # bool
    """

    def __init__(self):
        self._raw = AppConfig().get_all()

    def _int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._raw.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    def _bool(self, key: str, default: bool = False) -> bool:
        v = self._raw.get(key, str(default).lower())
        return v == "true"

    def _float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self._raw.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    # ---- 常用配置属性 ----
    @property
    def retrieval_top_k(self) -> int: return self._int("retrieval_top_k", 15)
    @property
    def retrieval_enable_query_expansion(self) -> bool: return self._bool("retrieval_enable_query_expansion")
    @property
    def memory_enabled(self) -> bool: return self._bool("memory_enabled", True)
    @property
    def auto_tag_enabled(self) -> bool: return self._bool("auto_tag_enabled", True)
    @property
    def reranker_enabled(self) -> bool: return self._bool("reranker_enabled")
    @property
    def reranker_top_k(self) -> int: return self._int("reranker_top_k", 3)
    @property
    def web_search_enabled(self) -> bool: return self._bool("web_search_enabled")
    @property
    def ocr_enabled(self) -> bool: return self._bool("ocr_enabled")
    @property
    def embedding_enabled(self) -> bool: return self._bool("embedding_enabled", True)
    @property
    def memory_dedup_threshold(self) -> float: return self._float("memory_dedup_threshold", 0.85)

"""配置管理 — KV 存储在 SQLite app_config 表中。"""

import json
from sqlalchemy import String, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from server.database import get_engine, get_session_ctx, DATA_DIR
from server.models.base import Base


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
}


EMBEDDING_CONFIG_KEYS = ("custom_embedding_model", "openai_embedding_model", "mlx_embedding_model")


def has_embedding_model(config: dict) -> bool:
    """判断配置中是否启用了外部 embedding 模型。"""
    return any(config.get(k) for k in EMBEDDING_CONFIG_KEYS)


class AppConfig:
    """运行时配置，读写 app_config 表。"""

    def get(self, key: str) -> str:
        with get_session_ctx() as session:
            row = session.get(AppConfigModel, key)
            if row is None:
                return DEFAULTS.get(key, "")
            return str(row.value.get("v", DEFAULTS.get(key, "")))

    def set(self, key: str, value: str) -> None:
        with get_session_ctx() as session:
            row = session.get(AppConfigModel, key)
            if row is None:
                row = AppConfigModel(key=key, value={"v": value}, updated_at=datetime.now(timezone.utc))
                session.add(row)
            else:
                row.value = {"v": value}
                row.updated_at = datetime.now(timezone.utc)
            session.commit()

    def get_all(self) -> dict:
        result = dict(DEFAULTS)
        with get_session_ctx() as session:
            rows = session.query(AppConfigModel).all()
            for row in rows:
                result[row.key] = str(row.value.get("v", DEFAULTS.get(row.key, "")))
        return result

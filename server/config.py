"""配置管理 — KV 存储在 SQLite app_config 表中。"""

import json
from sqlalchemy import String, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from server.database import get_engine, get_session, DATA_DIR
from server.models.base import Base


class AppConfigModel(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


DEFAULTS = {
    "llm_provider": "mlx",
    "mlx_chat_model": "",
    "mlx_embedding_model": "",
    "mlx_api_base": "http://localhost:8080/v1",
    "openai_api_key": "",
    "openai_chat_model": "gpt-4o-mini",
    "openai_embedding_model": "text-embedding-3-small",
    "openai_api_base": "https://api.openai.com/v1",
    "claude_api_key": "",
    "claude_chat_model": "claude-sonnet-4-6",
    "chunk_size": "800",
    "chunk_overlap": "100",
    "retrieval_top_k": "5",
}


class AppConfig:
    """运行时配置，读写 app_config 表。"""

    def get(self, key: str) -> str:
        with next(get_session()) as session:
            row = session.get(AppConfigModel, key)
            if row is None:
                return DEFAULTS.get(key, "")
            return str(row.value.get("v", DEFAULTS.get(key, "")))

    def set(self, key: str, value: str) -> None:
        with next(get_session()) as session:
            row = session.get(AppConfigModel, key)
            if row is None:
                row = AppConfigModel(key=key, value={"v": value})
                session.add(row)
            else:
                row.value = {"v": value}
                row.updated_at = datetime.now(timezone.utc)
            session.commit()

    def get_all(self) -> dict:
        result = dict(DEFAULTS)
        with next(get_session()) as session:
            rows = session.query(AppConfigModel).all()
            for row in rows:
                result[row.key] = str(row.value.get("v", DEFAULTS.get(row.key, "")))
        return result

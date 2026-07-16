"""Embedding 服务 — 封装 LLM embedding 接口。

支持两种模式：
1. 独立 embedding 配置（embedding_enabled=true）— 使用专用的 embedding API
2. 跟随对话 LLM 配置 — 回退到 LLMAdapter 的 embedding 方法
"""

import logging
from openai import OpenAI
from server.services.llm import LLMAdapter

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self, config: dict):
        self._config = config
        self._adapter = None
        self._standalone_client: OpenAI | None = None

        if config.get("embedding_enabled") == "true" and config.get("embedding_model"):
            base_url = config.get("embedding_api_base", "").strip()
            api_key = config.get("embedding_api_key", "").strip()
            if base_url:
                # API Key 解析: embedding_api_key > llm_api_key > 本地 dummy
                api_key = api_key or config.get("llm_api_key", "").strip() or "not-needed"
                self._standalone_client = OpenAI(base_url=base_url, api_key=api_key)
                self.model = config["embedding_model"]
                logger.info("Embedding 独立模式: model=%s base=%s", self.model, base_url)
                return

        # 回退：跟随对话 LLM
        self._adapter = LLMAdapter(config)
        logger.info("Embedding 跟随对话 LLM")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._standalone_client:
            resp = self._standalone_client.embeddings.create(
                model=self.model, input=texts,
            )
            return [d.embedding for d in resp.data]
        return self._adapter.embed(texts)

"""Embedding 服务 — 封装 LLM embedding 接口。"""

from server.services.llm import LLMAdapter


class Embedder:
    def __init__(self, config: dict):
        self._adapter = LLMAdapter(config)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._adapter.embed(texts)

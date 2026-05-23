"""Embedding 服务 — 调用 LLM embedding 接口。"""

from server.services.llm import LLMAdapter


class Embedder:
    def __init__(self, config: dict):
        self._adapter = LLMAdapter(config)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._adapter.client.embeddings.create(
            model=self._adapter.embedding_model,
            input=texts,
        )
        return [d.embedding for d in response.data]

"""LLM 适配器 — 支持 MLX / OpenAI / Claude / 第三方兼容接口。"""

from openai import OpenAI


class LLMAdapter:
    def __init__(self, config: dict):
        self._cfg = {k: v for k, v in config.items()}
        self.provider = self._cfg.get("llm_provider", "mlx")
        self._client = self._build_client()
        self.chat_model = self._get_chat_model()
        self.embedding_model = self._get_embedding_model()

    def _build_client(self) -> OpenAI:
        if self.provider == "mlx":
            return OpenAI(
                base_url=self._cfg.get("mlx_api_base", "http://localhost:8080/v1"),
                api_key="mlx",
            )
        if self.provider == "openai":
            return OpenAI(
                base_url=self._cfg.get("openai_api_base", "https://api.openai.com/v1"),
                api_key=self._cfg.get("openai_api_key", ""),
            )
        if self.provider == "claude":
            return OpenAI(
                base_url="https://api.anthropic.com/v1",
                api_key=self._cfg.get("claude_api_key", ""),
            )
        if self.provider == "custom":
            return OpenAI(
                base_url=self._cfg.get("custom_api_base", ""),
                api_key=self._cfg.get("custom_api_key", ""),
            )
        raise ValueError(f"不支持的 LLM provider: {self.provider}")

    def _get_chat_model(self) -> str:
        if self.provider == "mlx":
            return self._cfg.get("mlx_chat_model", "")
        if self.provider == "openai":
            return self._cfg.get("openai_chat_model", "gpt-4o-mini")
        if self.provider == "claude":
            return self._cfg.get("claude_chat_model", "claude-sonnet-4-6")
        if self.provider == "custom":
            return self._cfg.get("custom_chat_model", "")
        return ""

    def _get_embedding_model(self) -> str:
        if self.provider == "mlx":
            return self._cfg.get("mlx_embedding_model", "")
        if self.provider == "openai":
            return self._cfg.get("openai_embedding_model", "text-embedding-3-small")
        if self.provider == "custom":
            return self._cfg.get("custom_embedding_model", "")
        return ""

    @property
    def client(self) -> OpenAI:
        return self._client

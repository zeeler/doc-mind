"""LLM 适配器 — 支持 OpenAI 格式和 Anthropic 格式。"""

import json
import logging
from typing import AsyncIterator
from openai import OpenAI, AsyncOpenAI
import httpx

logger = logging.getLogger(__name__)


class LLMAdapter:
    def __init__(self, config: dict):
        self._cfg = {k: v for k, v in config.items()}
        self.provider = self._cfg.get("llm_provider", "mlx")
        self._client: OpenAI | None = None
        self._async_client: AsyncOpenAI | None = None
        self.chat_model = self._get_chat_model()
        self.embedding_model = self._get_embedding_model()
        self.api_type = self._get_api_type()

        logger.info(f"LLM 适配器: provider={self.provider}, api_type={self.api_type}, model={self.chat_model}")

    def _resolve_api_key(self) -> str:
        """统一解析 API Key：llm_api_key > 提供商专用 key > 默认值。"""
        unified = self._cfg.get("llm_api_key", "").strip()
        if unified:
            return unified
        if self.provider == "mlx":
            return "mlx"
        if self.provider == "openai":
            return self._cfg.get("openai_api_key", "")
        if self.provider == "claude":
            return self._cfg.get("claude_api_key", "")
        if self.provider == "custom":
            return self._cfg.get("custom_api_key", "")
        return ""

    def _get_api_type(self) -> str:
        """返回 API 格式类型: 'openai' 或 'anthropic'"""
        if self.provider == "custom":
            return self._cfg.get("custom_api_type", "openai")
        if self.provider == "claude":
            return "anthropic"
        return "openai"

    def _build_openai_client(self) -> OpenAI:
        if self.provider == "mlx":
            return OpenAI(
                base_url=self._cfg.get("mlx_api_base", "http://localhost:8080/v1"),
                api_key=self._resolve_api_key(),
            )
        if self.provider == "openai":
            return OpenAI(
                base_url=self._cfg.get("openai_api_base", "https://api.openai.com/v1"),
                api_key=self._resolve_api_key(),
            )
        if self.provider == "claude":
            return OpenAI(
                base_url="https://api.anthropic.com/v1",
                api_key=self._resolve_api_key(),
            )
        if self.provider == "custom":
            custom_base = self._cfg.get("custom_api_base", "")
            if self._cfg.get("custom_api_type", "openai") == "anthropic":
                # Anthropic 格式走 httpx 直连，不创建 OpenAI dummy client
                raise ValueError("Anthropic custom provider 不支持同步 OpenAI client，请使用 chat/completions 直连")
            return OpenAI(
                base_url=custom_base,
                api_key=self._resolve_api_key(),
            )
        raise ValueError(f"不支持的 LLM provider: {self.provider}")

    def _build_async_openai_client(self) -> AsyncOpenAI:
        if self.provider == "mlx":
            return AsyncOpenAI(
                base_url=self._cfg.get("mlx_api_base", "http://localhost:8080/v1"),
                api_key=self._resolve_api_key(),
            )
        if self.provider == "openai":
            return AsyncOpenAI(
                base_url=self._cfg.get("openai_api_base", "https://api.openai.com/v1"),
                api_key=self._resolve_api_key(),
            )
        if self.provider == "claude":
            return AsyncOpenAI(
                base_url="https://api.anthropic.com/v1",
                api_key=self._resolve_api_key(),
            )
        if self.provider == "custom":
            custom_base = self._cfg.get("custom_api_base", "")
            if self._cfg.get("custom_api_type", "openai") == "anthropic":
                return AsyncOpenAI(base_url=custom_base, api_key=self._resolve_api_key())
            return AsyncOpenAI(
                base_url=custom_base,
                api_key=self._resolve_api_key(),
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
        if self._client is None:
            self._client = self._build_openai_client()
        return self._client

    @property
    def async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            self._async_client = self._build_async_openai_client()
        return self._async_client

    # ---- 统一对话接口 ----

    def chat(self, messages: list[dict], temperature: float = 0.3) -> dict:
        """同步对话，返回 {"content": str, ...}。"""
        if self.api_type == "anthropic":
            return self._anthropic_chat(messages, temperature)
        return self._openai_chat(messages, temperature)

    async def chat_stream(self, messages: list[dict], temperature: float = 0.3) -> AsyncIterator[dict]:
        """流式对话，yield {"type": "token", "content": str} | {"type": "done"}。"""
        if self.api_type == "anthropic":
            async for chunk in self._anthropic_chat_stream(messages, temperature):
                yield chunk
        else:
            async for chunk in self._openai_chat_stream(messages, temperature):
                yield chunk

    def embed(self, texts: list[str]) -> list[list[float]]:
        """文本转向量。"""
        response = self.client.embeddings.create(
            model=self.embedding_model,
            input=texts,
        )
        return [d.embedding for d in response.data]

    # ---- OpenAI 格式 ----

    def _openai_chat(self, messages: list[dict], temperature: float) -> dict:
        response = self.client.chat.completions.create(
            model=self.chat_model,
            messages=messages,
            temperature=temperature,
        )
        return {"content": response.choices[0].message.content or ""}

    async def _openai_chat_stream(self, messages: list[dict], temperature: float):
        stream = await self.async_client.chat.completions.create(
            model=self.chat_model,
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:  # 部分兼容服务会发 usage-only chunk（choices 为空）
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield {"type": "token", "content": delta.content}
        yield {"type": "done"}

    # ---- Anthropic 格式 ----

    def _anthropic_headers(self) -> dict:
        api_key = self._resolve_api_key()
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _anthropic_messages_url(self) -> str:
        if self.provider == "claude":
            return "https://api.anthropic.com/v1/messages"
        base = self._cfg.get("custom_api_base", "").rstrip("/")
        return f"{base}/messages"

    def _to_anthropic_messages(self, messages: list[dict]) -> list[dict]:
        """将 OpenAI 格式 messages 转为 Anthropic 格式。"""
        result = []
        for m in messages:
            result.append({
                "role": m["role"],
                "content": [{"type": "text", "text": m["content"]}],
            })
        return result

    def _anthropic_chat(self, messages: list[dict], temperature: float) -> dict:
        url = self._anthropic_messages_url()
        headers = self._anthropic_headers()
        body = {
            "model": self.chat_model,
            "max_tokens": 4096,
            "temperature": temperature,
            "messages": self._to_anthropic_messages(messages),
        }

        timeout = int(self._cfg.get("llm_timeout", "300"))
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        # 提取文本内容
        parts = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return {"content": "".join(parts)}

    async def _anthropic_chat_stream(self, messages: list[dict], temperature: float):
        url = self._anthropic_messages_url()
        headers = self._anthropic_headers()
        body = {
            "model": self.chat_model,
            "max_tokens": 4096,
            "temperature": temperature,
            "messages": self._to_anthropic_messages(messages),
            "stream": True,
        }

        timeout = int(self._cfg.get("llm_timeout", "300"))
        async with httpx.AsyncClient(timeout=timeout) as http:
            async with http.stream("POST", url, headers=headers, json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            event = json.loads(line[6:])
                            if event.get("type") == "content_block_delta":
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    yield {"type": "token", "content": delta.get("text", "")}
                        except (json.JSONDecodeError, KeyError):
                            pass
        yield {"type": "done"}

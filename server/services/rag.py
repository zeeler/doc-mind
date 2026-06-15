"""RAG 编排 — 组装 prompt、调用 LLM、流式输出。"""

import asyncio
import logging
from typing import AsyncIterator
from server.services.llm import LLMAdapter
from server.services.web_search import WebSearchClient

logger = logging.getLogger("knowledge-base")


def _build_history_text(history: list[dict] | None) -> str:
    """将对话历史格式化为单段文本，注入 system message。"""
    if not history:
        return ""
    parts = []
    for h in history[-6:]:
        role = "用户" if h["role"] == "user" else "助手"
        parts.append(f"{role}：{h['content']}")
    if parts:
        return "## 对话历史\n" + "\n".join(parts)
    return ""


SYSTEM_PROMPT_BASE = "你是一个知识库助手。请根据参考资料回答用户问题。使用中文回答。"


def _build_messages(prompt: str, history: list[dict] | None = None,
                    memory_context: str = "") -> list[dict]:
    """Build the messages list with a single system message (Anthropic compatible)."""
    system_parts = [SYSTEM_PROMPT_BASE]
    if history:
        history_text = _build_history_text(history)
        if history_text:
            system_parts.append(history_text)
    if memory_context:
        system_parts.append(memory_context)
    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": prompt},
    ]


def build_qa_prompt(
    question: str,
    chunks: list[dict],
    web_sourced: bool = False,
) -> str:
    if not chunks:
        return (
            f"## 用户问题\n{question}\n\n"
            f"知识库中未找到相关内容。请基于你自身的知识如实回答，"
            f"并在回答末尾注明：\n"
            f"> 📚 *以上回答基于模型自身知识，未引用知识库文档。*"
        )
    if web_sourced:
        return _build_web_prompt(question, chunks)
    return _build_kb_prompt(question, chunks)


def _build_kb_prompt(question: str, chunks: list[dict]) -> str:
    doc_titles = list(dict.fromkeys(c["document_title"] for c in chunks if c.get("document_title")))

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        chunk_no = chunk.get("chunk_no", 0)
        context_parts.append(
            f"[{i}] 来源: {chunk['document_title']} (段落 {chunk_no})\n{chunk['content']}"
        )

    context = "\n\n".join(context_parts)
    doc_hint = ""
    if doc_titles:
        titles_str = "、".join(doc_titles[:3])
        doc_hint = f"\n以上参考资料来自你的知识库文档：{titles_str}。这些是用户已上传的个人文档内容。"

    return f"""## 参考资料
{context}{doc_hint}

## 要求
- 参考资料来自用户已上传的文档，优先使用其中的信息回答问题
- 理解对话历史中的上下文，结合当前问题给出连贯的回答
- 如果当前问题是对上一轮回答的追问或澄清，请基于历史上下文理解用户意图
- 即使信息分散在多个片段中，也要尽量综合整理，给出有价值的回答
- 回答中引用来源编号，如 [1]、[2]
- 如果参考资料覆盖了多个不同的要点或角度，请全面综合回答，不要遗漏
- 只有确实完全不相关时才说明无法回答，不要因为信息不完整就放弃
- 使用中文回答
- 在回答末尾必须添加信息来源说明，格式如下：
  > 📚 **信息来源**：知识库文档《书名1》、《书名2》

## 用户问题
{question}"""


def _build_web_prompt(question: str, chunks: list[dict]) -> str:
    context_parts = []
    doc_titles = []
    for i, chunk in enumerate(chunks, 1):
        url = chunk.get("url") or chunk.get("file_name", "")
        context_parts.append(
            f"[{i}] 标题: {chunk['document_title']}\n"
            f"链接: {url}\n"
            f"内容: {chunk['content']}"
        )
        title = chunk.get("document_title", "")
        if title:
            doc_titles.append(title)

    context = "\n\n".join(context_parts)
    titles_str = "、".join(list(dict.fromkeys(doc_titles))[:3]) if doc_titles else "互联网"

    return f"""## 互联网搜索结果
{context}

## 要求
- 优先使用搜索结果中的信息回答问题
- 理解对话历史中的上下文，结合当前问题给出连贯的回答
- 回答中引用来源编号，如 [1]、[2]，并在引用处附上对应的链接 URL
- 综合多个来源的信息，给出全面的回答
- 如果搜索结果无法覆盖问题，可以结合自身知识补充，但请注明哪部分来自自身知识
- 使用中文回答
- 在回答末尾必须添加信息来源说明，格式如下：
  > 🌐 **信息来源**：互联网搜索（{titles_str} 等）

## 用户问题
{question}"""


def format_citations(chunks: list[dict], web_sourced: bool = False) -> list[dict]:
    seen_ids: set[str] = set()
    result = []
    for c in chunks:
        chunk_id = c.get("chunk_id", "")
        if chunk_id and chunk_id in seen_ids:
            continue
        if chunk_id:
            seen_ids.add(chunk_id)
        is_web = web_sourced or c.get("match_type") == "web"
        citation: dict = {
            "source_type": "web_search" if is_web else "document_chunk",
            "chunk_id": c.get("chunk_id", ""),
            "document_id": c.get("document_id", ""),
            "document_title": c.get("document_title", ""),
            "file_name": c.get("file_name", ""),
            "chunk_no": c.get("chunk_no", 0),
            "excerpt": c.get("content", "")[:300],
        }
        if is_web:
            citation["url"] = c.get("url") or c.get("file_name", "")
        result.append(citation)
    return result


class RAGService:
    def __init__(self, retriever, config: dict):
        self.retriever = retriever
        self.llm = LLMAdapter(config)
        self.config = config
        self._web_search_client: WebSearchClient | None = None

    @property
    def _web_search(self) -> WebSearchClient | None:
        """延迟初始化 WebSearchClient，仅在启用且配置了 Key 时可用。"""
        if self._web_search_client is not None:
            return self._web_search_client
        enabled = self.config.get("web_search_enabled", "false") == "true"
        api_key = self.config.get("tavily_api_key", "")
        if enabled and api_key:
            max_results = int(self.config.get("web_search_max_results", "5"))
            self._web_search_client = WebSearchClient(api_key, max_results)
        return self._web_search_client

    def _is_web_search_needed(self, chunks: list[dict]) -> bool:
        """判断是否需要触发网络搜索：KB 结果太少或相关性太低。

        评分体系因 match_type 而异：
        - rerank_score: 0–1（余弦相似度）
        - RRF 融合: ~0.008–0.017（k=60）
        - FTS5: 0.09–0.5（1/(1+rank)）
        因此需根据实际分数范围动态判断，而非使用固定阈值。
        """
        client = self._web_search
        if client is None:
            return False
        if not chunks:
            return True

        scores = [c.get("score", 0.0) for c in chunks]
        avg = sum(scores) / len(scores)
        max_score = max(scores)

        # 有 reranker 精排分数（范围 0–1）：分数 < 0.3 视为低质量
        has_rerank = any("rerank_score" in c for c in chunks)
        if has_rerank:
            good = [s for s in scores if s > 0.3]
            return len(good) < 2

        # RRF 融合分数（范围 ~0.008–0.017）：avg < 0.01 或 max < 0.012 视为低质量
        if max_score < 0.1:
            good = [s for s in scores if s > 0.01]
            return len(good) < 2

        # FTS5 纯文本分数（范围 0.09–0.5）：avg < 0.15 视为低质量
        good = [s for s in scores if s > 0.15]
        return len(good) < 2

    def ask_sync(self, question: str, history: list[dict] | None = None,
                 memory_context: str = "") -> dict:
        chunks = self.retriever.retrieve(question)
        web_sourced = False

        if self._is_web_search_needed(chunks):
            ws = self._web_search
            if ws:
                web_chunks = ws.search(question)
                if web_chunks:
                    logger.info("网络搜索补充: %d 条结果", len(web_chunks))
                    # KB 结果不足时用网络搜索代替，否则混合使用
                    if not chunks:
                        chunks = web_chunks
                        web_sourced = True
                    else:
                        chunks = chunks + web_chunks
                        web_sourced = False  # 混合结果仍以 KB 为主

        prompt = build_qa_prompt(question, chunks, web_sourced=web_sourced)

        messages = _build_messages(prompt, history=history, memory_context=memory_context)

        result = self.llm.chat(messages=messages, temperature=0.3)
        citations = format_citations(chunks, web_sourced=web_sourced)
        return {"answer": result["content"], "citations": citations}

    async def ask_stream(self, question: str, history: list[dict] | None = None,
                         memory_context: str = "") -> AsyncIterator[dict]:
        loop = asyncio.get_running_loop()
        chunks = await loop.run_in_executor(None, self.retriever.retrieve, question)
        web_sourced = False

        if self._is_web_search_needed(chunks):
            ws = self._web_search
            if ws:
                web_chunks = await loop.run_in_executor(None, ws.search, question)
                if web_chunks:
                    if not chunks:
                        chunks = web_chunks
                        web_sourced = True
                    else:
                        chunks = chunks + web_chunks
                        web_sourced = False

        prompt = build_qa_prompt(question, chunks, web_sourced=web_sourced)

        messages = _build_messages(prompt, history=history, memory_context=memory_context)

        async for chunk in self.llm.chat_stream(messages=messages, temperature=0.3):
            yield chunk
        yield {"type": "citations", "data": format_citations(chunks, web_sourced=web_sourced)}
